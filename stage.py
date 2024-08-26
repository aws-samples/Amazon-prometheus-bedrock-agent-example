import boto3
import re
import json
import subprocess
import sys
import argparse
from time import sleep
from shutil import copyfile, make_archive
from tempfile import mkdtemp

def amp_workspace_id_type(arg_value, pat=re.compile('^ws-[{]?[0-9a-fA-F]{8}-([0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}[}]?$')):
    if not pat.match(arg_value):
        raise argparse.ArgumentTypeError(f"Invalid workspace ID format: {arg_value}")
    return arg_value

def amp_region_type(arg_value):
    if arg_value == '<Default>':
        return boto3.Session().region_name
    return arg_value

parser = argparse.ArgumentParser()
parser.add_argument('--amp-workspace-id', type=amp_workspace_id_type, required=True, help='Amazon Managed Service for Prometheus (AMP) workspace ID')
parser.add_argument('--amp-region', type=amp_region_type, default=boto3.Session().region_name, help='Amazon Managed Service for Prometheus (AMP) region')
parser.add_argument('--resource-prefix', type=str, default='amp-bedrock-agent-', help='Resource prefix for AWS Resources')

boto3_session = boto3.Session()
account_id = boto3_session.client('sts').get_caller_identity().get('Account')

args = parser.parse_args()

s3_client = boto3_session.client('s3', region_name=args.amp_region)
iam_client = boto3_session.client('iam', region_name=args.amp_region)
lambda_client = boto3_session.client('lambda', region_name=args.amp_region)

tmp_dir = mkdtemp(prefix=args.resource_prefix)

for file in ['index.py', 'amp_apis.py']:
    copyfile(f"./lambda/amp-agent/{file}", f"{tmp_dir}/{file}")

# Install required packages to disc
subprocess.check_call([sys.executable, "-m", "pip", "install", '--upgrade', 'pip'])
for package in ['requests_aws4auth','requests']:
    subprocess.check_call([sys.executable, "-m", "pip", "install", package, '--quiet', '-t', tmp_dir])

make_archive(f"{tmp_dir}/lambda", 'zip',tmp_dir)

role_name = f"{args.resource_prefix}lambda-role"
print(f"role_name: {role_name}")
lambda_function_name = f"{args.resource_prefix}function"
print(f"lambda_function_name: {lambda_function_name}")

try:
    iam_client.create_role(
        RoleName=role_name,
        AssumeRolePolicyDocument='{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"lambda.amazonaws.com"},"Action":"sts:AssumeRole"}]}'
    )
except iam_client.exceptions.EntityAlreadyExistsException:
    print ("Roll already exists.  No action needed.")
except Exception as e:
    print (e)

iam_client.attach_role_policy(
    RoleName=role_name,
    PolicyArn='arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole'
)

amp_policy = {
    "Version": "2012-10-17",
    "Statement": [
        {
            "Sid": "AMPQuery",
            "Effect": "Allow",
            "Action": "aps:QueryMetrics",
            "Resource": f"arn:aws:aps:{args.amp_region}:{account_id}:workspace/{args.amp_workspace_id}"
        }
    ]
}
print(f"amp_policy: {amp_policy}")
iam_client.put_role_policy(
    RoleName=f"{args.resource_prefix}lambda-role",
    PolicyName='AMP',
    PolicyDocument=json.dumps(amp_policy)
)
while True:
    try:
        lambda_client.create_function(
            FunctionName=lambda_function_name,
            Runtime='python3.12',
            Handler='index.lambda_handler',
            Code={'ZipFile': open(f"{tmp_dir}/lambda.zip", 'rb').read()},
            Role=f"arn:aws:iam::{account_id}:role/{role_name}",
            Environment={
                'Variables': {
                    'AMP_WORKSPACE_ID': args.amp_workspace_id,
                    'AMP_REGION': args.amp_region
                }
            }
        )
        break
    except lambda_client.exceptions.ResourceConflictException:
        print ("Function already exists.  No action needed.")
        break
    except lambda_client.exceptions.InvalidParameterValueException:
        sleep (5)
    except Exception as e:
        print (e)

lambda_client.add_permission(
    FunctionName=lambda_function_name,
    StatementId="bedrock-access",
    Action='lambda:InvokeFunction',
    Principal='bedrock.amazonaws.com',
    SourceAccount=account_id,
    SourceArn=f"arn:aws:bedrock:{args.amp_region}:{account_id}:agent/*"
)
