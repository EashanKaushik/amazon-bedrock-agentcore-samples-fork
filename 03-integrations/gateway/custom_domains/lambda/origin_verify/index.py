# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
import logging
import os

logger = logging.getLogger()
logger.setLevel(logging.INFO)

EXPECTED_HEADER = os.environ["ORIGIN_VERIFY_HEADER"]
EXPECTED_VALUE = os.environ["ORIGIN_VERIFY_VALUE"]


def lambda_handler(event, context):
    """
    AgentCore Gateway REQUEST interceptor that validates the custom origin
    header added by CloudFront. Rejects requests that bypass CloudFront
    with a 403.

    Configure on your gateway with passRequestHeaders enabled.
    """
    mcp_data = event.get("mcp", {})

    # REQUEST interceptor — validate origin header
    gateway_request = mcp_data.get("gatewayRequest", {})
    headers = gateway_request.get("headers", {})
    header_value = headers.get(EXPECTED_HEADER, "")
    request_body = gateway_request.get("body", {})
    request_id = request_body.get("id")

    if header_value != EXPECTED_VALUE:
        logger.warning("Origin verification failed")
        return {
            "interceptorOutputVersion": "1.0",
            "mcp": {
                "transformedGatewayResponse": {
                    "statusCode": 403,
                    "body": {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "error": {
                            "code": -32600,
                            "message": "Forbidden",
                        },
                    },
                }
            },
        }

    mcp_method = request_body.get("method", "unknown")
    logger.info(f"Verified request — MCP method: {mcp_method}")
    return {
        "interceptorOutputVersion": "1.0",
        "mcp": {
            "transformedGatewayRequest": {
                "body": request_body,
            }
        },
    }
