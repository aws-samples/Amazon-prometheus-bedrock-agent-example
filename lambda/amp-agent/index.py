"""Bedrock Agent Lambda Handler."""
import os
import json
import logging
from amp_apis import AMP

logger = logging.getLogger(__name__)

amp = AMP(
    workspace_id=os.environ.get('AMP_WORKSPACE_ID'),
    region=os.environ.get('AMP_REGION')
    )

def build_bedrock_response(event, response_text):
    """Return answer to Bedrock Agent in expected format."""
    return {
        "messageVersion": "1.0",
        "response": {
            "actionGroup": event.get('actionGroup'),
            "function": event.get('function'),
            "functionResponse": {
                "responseBody": {
                    "TEXT": { 
                        "body": response_text
                    }
                }
            }
        }
    }

def lambda_handler(event, context):
    """Handle incoming BedRock Agent payload."""
    logger.debug(json.dumps(event))
    api_name = event.get('actionGroup')
    print(f"api_name per actionGroup: {api_name}")
    if api_name == 'query':
        api_response = amp.amp_query(event)
        bedrock_response_data = api_response.content
    else:
        bedrock_response_data = f"Unknown API: {api_name}"
    return build_bedrock_response(event, bedrock_response_data)
