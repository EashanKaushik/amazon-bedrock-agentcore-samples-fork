#!/usr/bin/env python3
import os

import aws_cdk as cdk

from custom_domains.custom_domains_stack import CustomDomainsStack


app = cdk.App()
CustomDomainsStack(
    app,
    "CustomDomainsStack",
    env=cdk.Environment(account="528043283031", region="us-east-1"),
)

app.synth()
