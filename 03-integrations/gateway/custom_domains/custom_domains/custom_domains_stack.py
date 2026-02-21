# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
import aws_cdk as cdk
from aws_cdk import (
    CfnOutput,
    RemovalPolicy,
    Stack,
    aws_certificatemanager as acm,
    aws_cloudfront as cloudfront,
    aws_cloudfront_origins as origins,
    aws_cloudwatch as cw,
    aws_cloudwatch_actions as cw_actions,
    aws_iam as iam,
    aws_lambda as _lambda,
    aws_route53 as route53,
    aws_route53_targets as targets,
    aws_s3 as s3,
    aws_secretsmanager as secretsmanager,
    aws_sns as sns,
    aws_wafv2 as wafv2,
)
from cdk_nag import NagSuppressions, NagPackSuppression
from constructs import Construct


class CustomDomainsStack(Stack):

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        domain_name = self.node.get_context("domain_name")
        authorization_server = self.node.get_context("authorization_server")
        gateway_hostname = self.node.get_context("gateway_hostname")
        # Custom origin header — gateway should reject requests without this
        origin_secret = "X-AgentCore-Origin-Verify"
        origin_verify_secret = secretsmanager.Secret(
            self,
            "OriginVerifySecret",
            description="CloudFront origin verification header value",
            generate_secret_string=secretsmanager.SecretStringGenerator(
                exclude_punctuation=True,
                password_length=32,
            ),
        )
        NagSuppressions.add_resource_suppressions(
            origin_verify_secret,
            [
                NagPackSuppression(
                    id="AwsSolutions-SMG4",
                    reason=(
                        "OriginVerifySecret is a static CloudFront origin-verification "
                        "header, not a rotatable credential. Rotating it would require "
                        "coordinated atomic updates across CloudFront, Secrets Manager, "
                        "and the Lambda environment variable."
                    ),
                ),
            ],
        )
        origin_secret_value = origin_verify_secret.secret_value.unsafe_unwrap()

        # Step 1: Import existing Route 53 hosted zone
        hosted_zone = route53.PublicHostedZone.from_lookup(
            self,
            "HostedZone",
            domain_name=domain_name,
        )

        # Step 2: ACM certificate (must be us-east-1 for CloudFront)
        certificate = acm.Certificate(
            self,
            "SSLCertificate",
            domain_name=domain_name,
            validation=acm.CertificateValidation.from_dns(hosted_zone),
        )
        certificate.apply_removal_policy(RemovalPolicy.RETAIN)

        # WAF Web ACL (must be us-east-1 for CloudFront)
        web_acl = wafv2.CfnWebACL(
            self,
            "WebAcl",
            default_action=wafv2.CfnWebACL.DefaultActionProperty(allow={}),
            scope="CLOUDFRONT",
            visibility_config=wafv2.CfnWebACL.VisibilityConfigProperty(
                cloud_watch_metrics_enabled=True,
                metric_name="AgentCoreGatewayWaf",
                sampled_requests_enabled=True,
            ),
            rules=[
                wafv2.CfnWebACL.RuleProperty(
                    name="AWSManagedRulesCommonRuleSet",
                    priority=1,
                    override_action=wafv2.CfnWebACL.OverrideActionProperty(none={}),
                    statement=wafv2.CfnWebACL.StatementProperty(
                        managed_rule_group_statement=wafv2.CfnWebACL.ManagedRuleGroupStatementProperty(
                            vendor_name="AWS",
                            name="AWSManagedRulesCommonRuleSet",
                        )
                    ),
                    visibility_config=wafv2.CfnWebACL.VisibilityConfigProperty(
                        cloud_watch_metrics_enabled=True,
                        metric_name="CommonRuleSet",
                        sampled_requests_enabled=True,
                    ),
                ),
                wafv2.CfnWebACL.RuleProperty(
                    name="AWSManagedRulesKnownBadInputsRuleSet",
                    priority=2,
                    override_action=wafv2.CfnWebACL.OverrideActionProperty(none={}),
                    statement=wafv2.CfnWebACL.StatementProperty(
                        managed_rule_group_statement=wafv2.CfnWebACL.ManagedRuleGroupStatementProperty(
                            vendor_name="AWS",
                            name="AWSManagedRulesKnownBadInputsRuleSet",
                        )
                    ),
                    visibility_config=wafv2.CfnWebACL.VisibilityConfigProperty(
                        cloud_watch_metrics_enabled=True,
                        metric_name="KnownBadInputs",
                        sampled_requests_enabled=True,
                    ),
                ),
                wafv2.CfnWebACL.RuleProperty(
                    name="RateLimit",
                    priority=3,
                    action=wafv2.CfnWebACL.RuleActionProperty(block={}),
                    statement=wafv2.CfnWebACL.StatementProperty(
                        rate_based_statement=wafv2.CfnWebACL.RateBasedStatementProperty(
                            limit=2000,
                            aggregate_key_type="IP",
                        )
                    ),
                    visibility_config=wafv2.CfnWebACL.VisibilityConfigProperty(
                        cloud_watch_metrics_enabled=True,
                        metric_name="RateLimit",
                        sampled_requests_enabled=True,
                    ),
                ),
            ],
        )

        # S3 bucket for CloudFront access logs
        log_bucket = s3.Bucket(
            self,
            "CloudFrontLogsBucket",
            encryption=s3.BucketEncryption.S3_MANAGED,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,
            object_ownership=s3.ObjectOwnership.OBJECT_WRITER,
            lifecycle_rules=[
                s3.LifecycleRule(expiration=cdk.Duration.days(90)),
            ],
        )

        NagSuppressions.add_resource_suppressions(
            log_bucket,
            [
                NagPackSuppression(
                    id="AwsSolutions-S1",
                    reason=(
                        "This IS the access-log destination bucket. Enabling access "
                        "logging on a log bucket creates circular logging."
                    ),
                ),
            ],
        )

        # SNS topic for alarms
        alarm_topic = sns.Topic(
            self, "AlarmTopic", display_name="AgentCore Gateway Alarms"
        )
        alarm_topic.add_to_resource_policy(
            iam.PolicyStatement(
                sid="EnforceSSL",
                effect=iam.Effect.DENY,
                principals=[iam.AnyPrincipal()],
                actions=["sns:Publish"],
                resources=[alarm_topic.topic_arn],
                conditions={
                    "Bool": {"aws:SecureTransport": "false"},
                },
            )
        )

        # Lambda@Edge to rewrite OAuth protected resource response
        oauth_rewrite_fn = cloudfront.experimental.EdgeFunction(
            self,
            "OAuthRewriteV2",
            runtime=_lambda.Runtime.PYTHON_3_14,
            handler="index.handler",
            code=_lambda.Code.from_inline(
                "\n".join(
                    [
                        "import json",
                        "def handler(event, context):",
                        "    response = event['Records'][0]['cf']['response']",
                        "    request = event['Records'][0]['cf']['request']",
                        "    if request.get('uri') == '/.well-known/oauth-protected-resource':",
                        "        body = json.dumps({",
                        f"            'authorization_servers': ['{authorization_server}'],",
                        f"            'resource': 'https://{domain_name}/mcp'",
                        "        })",
                        "        return {",
                        "            'status': '200',",
                        "            'statusDescription': 'OK',",
                        "            'headers': {",
                        "                'content-type': [{'key': 'Content-Type', 'value': 'application/json'}],",
                        "                'cache-control': [{'key': 'Cache-Control', 'value': 'no-store'}],",
                        "            },",
                        "            'body': body,",
                        "        }",
                        "    return response",
                    ]
                )
            ),
        )

        # Lambda@Edge to rewrite WWW-Authenticate header on 401 responses
        www_auth_rewrite_fn = cloudfront.experimental.EdgeFunction(
            self,
            "WwwAuthRewrite",
            runtime=_lambda.Runtime.PYTHON_3_14,
            handler="index.handler",
            code=_lambda.Code.from_inline(
                "\n".join(
                    [
                        "def handler(event, context):",
                        "    response = event['Records'][0]['cf']['response']",
                        "    if response.get('status') == '401':",
                        "        headers = response.get('headers', {})",
                        "        if 'www-authenticate' in headers:",
                        "            for h in headers['www-authenticate']:",
                        f"                h['value'] = h['value'].replace(",
                        f"                    'https://{gateway_hostname}',",
                        f"                    'https://{domain_name}'",
                        "                )",
                        "    return response",
                    ]
                )
            ),
        )

        # Origins
        gateway_origin_with_mcp = origins.HttpOrigin(
            gateway_hostname,
            protocol_policy=cloudfront.OriginProtocolPolicy.HTTPS_ONLY,
            origin_path="/mcp",
            custom_headers={origin_secret: origin_secret_value},
        )
        gateway_origin_passthrough = origins.HttpOrigin(
            gateway_hostname,
            protocol_policy=cloudfront.OriginProtocolPolicy.HTTPS_ONLY,
            custom_headers={origin_secret: origin_secret_value},
        )

        # Step 3: CloudFront distribution
        distribution = cloudfront.Distribution(
            self,
            "Distribution",
            default_behavior=cloudfront.BehaviorOptions(
                origin=gateway_origin_with_mcp,
                viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.HTTPS_ONLY,
                cache_policy=cloudfront.CachePolicy.CACHING_DISABLED,
                allowed_methods=cloudfront.AllowedMethods.ALLOW_ALL,
                edge_lambdas=[
                    cloudfront.EdgeLambda(
                        function_version=www_auth_rewrite_fn.current_version,
                        event_type=cloudfront.LambdaEdgeEventType.ORIGIN_RESPONSE,
                    )
                ],
            ),
            additional_behaviors={
                "/.well-known/*": cloudfront.BehaviorOptions(
                    origin=gateway_origin_passthrough,
                    viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.HTTPS_ONLY,
                    cache_policy=cloudfront.CachePolicy.CACHING_DISABLED,
                    allowed_methods=cloudfront.AllowedMethods.ALLOW_ALL,
                    edge_lambdas=[
                        cloudfront.EdgeLambda(
                            function_version=oauth_rewrite_fn.current_version,
                            event_type=cloudfront.LambdaEdgeEventType.ORIGIN_RESPONSE,
                        )
                    ],
                ),
                "/mcp": cloudfront.BehaviorOptions(
                    origin=gateway_origin_passthrough,
                    viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.HTTPS_ONLY,
                    cache_policy=cloudfront.CachePolicy.CACHING_DISABLED,
                    allowed_methods=cloudfront.AllowedMethods.ALLOW_ALL,
                    edge_lambdas=[
                        cloudfront.EdgeLambda(
                            function_version=www_auth_rewrite_fn.current_version,
                            event_type=cloudfront.LambdaEdgeEventType.ORIGIN_RESPONSE,
                        )
                    ],
                ),
            },
            domain_names=[domain_name],
            certificate=certificate,
            web_acl_id=web_acl.attr_arn,
            enable_logging=True,
            log_bucket=log_bucket,
            log_file_prefix="cloudfront/",
            geo_restriction=cloudfront.GeoRestriction.allowlist(
                # US + Canada
                "US",
                "CA",
                # EU member states
                "AT",
                "BE",
                "BG",
                "HR",
                "CY",
                "CZ",
                "DK",
                "EE",
                "FI",
                "FR",
                "DE",
                "GR",
                "HU",
                "IE",
                "IT",
                "LV",
                "LT",
                "LU",
                "MT",
                "NL",
                "PL",
                "PT",
                "RO",
                "SK",
                "SI",
                "ES",
                "SE",
            ),
        )

        # CloudWatch alarms
        alarm_5xx = cw.Alarm(
            self,
            "5xxErrorAlarm",
            metric=distribution.metric5xx_error_rate(),
            threshold=5,
            evaluation_periods=3,
            comparison_operator=cw.ComparisonOperator.GREATER_THAN_THRESHOLD,
            alarm_description="CloudFront 5xx error rate > 5%",
        )
        alarm_5xx.add_alarm_action(cw_actions.SnsAction(alarm_topic))

        alarm_4xx = cw.Alarm(
            self,
            "4xxErrorAlarm",
            metric=distribution.metric4xx_error_rate(),
            threshold=20,
            evaluation_periods=3,
            comparison_operator=cw.ComparisonOperator.GREATER_THAN_THRESHOLD,
            alarm_description="CloudFront 4xx error rate > 20%",
        )
        alarm_4xx.add_alarm_action(cw_actions.SnsAction(alarm_topic))

        # Sample gateway REQUEST interceptor Lambda — validates the custom
        # origin header so the gateway rejects requests that bypass CloudFront.
        # Attach this as a REQUEST interceptor on your AgentCore Gateway with
        # passRequestHeaders enabled.
        origin_verify_fn = _lambda.Function(
            self,
            "OriginVerifyInterceptor",
            runtime=_lambda.Runtime.PYTHON_3_14,
            handler="index.lambda_handler",
            code=_lambda.Code.from_asset("lambda/origin_verify"),
            environment={
                "ORIGIN_VERIFY_HEADER": origin_secret,
                "ORIGIN_VERIFY_VALUE": origin_secret_value,
            },
        )

        for fn in [oauth_rewrite_fn, www_auth_rewrite_fn, origin_verify_fn]:
            NagSuppressions.add_resource_suppressions(
                fn,
                [
                    NagPackSuppression(
                        id="AwsSolutions-IAM4",
                        reason=(
                            "AWSLambdaBasicExecutionRole is the minimum managed policy "
                            "required for Lambda CloudWatch Logs access."
                        ),
                    ),
                ],
                apply_to_children=True,
            )

        # Outputs
        CfnOutput(
            self, "DistributionDomain", value=distribution.distribution_domain_name
        )
        CfnOutput(self, "CustomDomain", value=f"https://{domain_name}/mcp")
        CfnOutput(self, "AlarmTopicArn", value=alarm_topic.topic_arn)
        CfnOutput(self, "OriginVerifyHeader", value=origin_secret)
        CfnOutput(self, "OriginVerifySecretArn", value=origin_verify_secret.secret_arn)
        CfnOutput(
            self,
            "OriginVerifyInterceptorArn",
            value=origin_verify_fn.function_arn,
        )

        # Step 4: Route 53 A record pointing to CloudFront
        route53.ARecord(
            self,
            "AliasRecord",
            zone=hosted_zone,
            record_name=domain_name,
            target=route53.RecordTarget.from_alias(
                targets.CloudFrontTarget(distribution)
            ),
        )
