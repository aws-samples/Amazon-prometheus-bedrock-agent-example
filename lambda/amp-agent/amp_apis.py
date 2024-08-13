"""Custom AMP API signer"""
from datetime import datetime, timedelta
import requests
import logging
import boto3
import os
from botocore.session import Session
from requests_aws4auth import AWS4Auth

logger = logging.getLogger(__name__)
amp_client = boto3.client('amp')

class AMP():
    """Custom AMP API signer"""
    def __init__(self, workspace_id:str = None, region:str = None, base_amp_url:str = None):
        self.region = region
        self.workspace_id = workspace_id
        if self.region is None:
            self.region = os.environ.get('AMP_REGION', 'us-east-1')
        self.base_amp_url = f"https://aps-workspaces.{self.region}.amazonaws.com/workspaces/{workspace_id}"
        logger.debug(
            "workspace_id: %s | region: %s | base_amp_url: %s",
            self.workspace_id,
            self.region,
            self.base_amp_url)

    def _auth(self):
        """Create sigv4 auth for requests."""
        credentials = Session().get_credentials()
        return AWS4Auth(
            credentials.access_key,
            credentials.secret_key,
            self.region,
            'aps',
            session_token = credentials.token
        )

    def _parse_parameters(self, parameter_name, parameters):
        """Get specific parameter value"""
        for parameter in parameters:
            if parameter['name'] == parameter_name:
                return parameter['value']
        return None


    def amp_query(self, event):
        """Make a signed API to AMP for query or query_range"""
        params, api_name = self._amp_query_params(event)
        logger.info ("Params: %s", params)
        endpoint = f"{self.base_amp_url}/api/v1/{api_name}"
        auth = self._auth()
        logger.debug(
            "__BedRock sigv4: endpoint: %s | params: %s | auth: %s",
            endpoint,
            params,
            auth)
        api_response = requests.get(
            url=endpoint,
            auth=auth,
            params=params,
            timeout=5
        )
        logger.info(
            "__BedRock Response: Response Code: %s | %s",
            api_response.status_code,
            api_response.content)

        return api_response

    def _amp_query_params(self, event):
        """Convert BedRock input into BODY for a query/query_range API"""
        print(f"start _amp_query_params: {event}")
        params = {
            "query": self._parse_parameters('query', event.get('parameters',{})),
            "step": event.get('Step','1h')
        }        
        param_start_time = self._parse_parameters('start_time', event.get('parameters',{}))
        param_end_time = self._parse_parameters('end_time', event.get('parameters',{}))

        # If either a start or end time is defined, make a query_range call instead of query
        if param_end_time and param_start_time:
            api_name = 'query'
        else:
            api_name = 'query_range'
            if param_end_time is None:
                logger.debug("Default value for end_time")
                params['end'] = datetime.now().timestamp()
            else:
                logger.debug( "Converting Param Timestamp for end_time")
                try:
                    params['end'] = datetime.fromtimestamp(int(param_end_time)).timestamp()
                except Exception as err:
                    logger.error(err)
                    params['end'] = datetime.now().timestamp()
            if param_start_time is None:
                print("Default value for start_time")
                params['start'] = (datetime.now()-timedelta(days=4)).timestamp()
            else:
                print( "Converting Param Timestamp for start_time")
                try:
                    params['start'] = datetime.fromtimestamp(int(param_start_time)).timestamp()
                except Exception as err:
                    logger.error(err)
                    params['start'] = (datetime.now()-timedelta(days=4)).timestamp()
        return params, api_name
