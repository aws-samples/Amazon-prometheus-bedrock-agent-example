--------------------------------------------------------------------------------
Amazon Prometheus Bedrock Agent Example
--------------------------------------------------------------------------------


Creates a Zip with additional python modules and code
Creates an IAM role and Lambda function for BedRock Agent to query AMP

## Requirements
python3   
pip  <- Must be part of path or script will fail  
boto3  

## How to 
clone repo:
> git clone https://github.com/aws-samples/Amazon-prometheus-bedrock-agent-example.git

Run Stage script
> python3 ./lambda/amp-agent/stage.py --amp-workspace-id <amp-workspace-id>  

Example:
> python3 ./stage.py --amp-workspace-id ws-11111111-1111-1111-1111-111111111111
