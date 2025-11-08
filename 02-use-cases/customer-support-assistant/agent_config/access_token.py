import os
from bedrock_agentcore.identity.auth import requires_access_token


@requires_access_token(
    provider_name=os.environ.get("GATEWAY_PROVIDER_NAME"),
    scopes=[],  # Optional unless required
    auth_flow="M2M",
)
async def get_gateway_access_token(access_token: str):
    return access_token
