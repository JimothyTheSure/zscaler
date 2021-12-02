# File: zscaler_connector.py
#
# Copyright (c) 2017-2021 Splunk Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software distributed under
# the License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND,
# either express or implied. See the License for the specific language governing permissions
# and limitations under the License.
#
#
# Phantom App imports
import phantom.app as phantom
from phantom.base_connector import BaseConnector
from phantom.action_result import ActionResult

import time
import json
import requests
import sys
from bs4 import BeautifulSoup, UnicodeDammit
from zscaler_consts import *


class RetVal(tuple):
    def __new__(cls, val1, val2):
        return tuple.__new__(RetVal, (val1, val2))


class ZscalerConnector(BaseConnector):

    def __init__(self):

        # Call the BaseConnectors init first
        super(ZscalerConnector, self).__init__()
        self._state = None
        self._base_url = None
        self._response = None  # The most recent response object
        self._headers = None
        self._category = None

    def _handle_py_ver_compat_for_input_str(self, input_str):
        """
        This method returns the encoded|original string based on the Python version.

        :param python_version: Information of the Python version
        :param input_str: Input string to be processed
        :return: input_str (Processed input string based on following logic 'input_str - Python 3; encoded input_str - Python 2')
        """

        try:
            if input_str and self._python_version == 2:
                input_str = UnicodeDammit(input_str).unicode_markup.encode('utf-8')
        except:
            self.debug_print("Error occurred while handling python 2to3 compatibility for the input string")

        return input_str

    def _get_error_message_from_exception(self, e):
        """ This method is used to get appropriate error message from the exception.
        :param e: Exception object
        :return: error message
        """

        error_msg = ZSCALER_ERROR_MESSAGE
        error_code = ZSCALER_ERROR_CODE_MESSAGE
        try:
            if hasattr(e, "args"):
                if len(e.args) > 1:
                    error_code = e.args[0]
                    error_msg = e.args[1]
                elif len(e.args) == 1:
                    error_code = ZSCALER_ERROR_CODE_MESSAGE
                    error_msg = e.args[0]
            else:
                error_code = ZSCALER_ERROR_CODE_MESSAGE
                error_msg = ZSCALER_ERROR_MESSAGE
        except:
            error_code = ZSCALER_ERROR_CODE_MESSAGE
            error_msg = ZSCALER_ERROR_MESSAGE

        try:
            error_msg = self._handle_py_ver_compat_for_input_str(error_msg)
        except TypeError:
            error_msg = TYPE_ERROR_MSG
        except:
            error_msg = ZSCALER_ERROR_MESSAGE

        try:
            if error_code in ZSCALER_ERROR_CODE_MESSAGE:
                error_text = "Error Message: {0}".format(error_msg)
            else:
                error_text = "Error Code: {0}. Error Message: {1}".format(error_code, error_msg)
        except:
            self.debug_print("Error occurred while parsing error message")
            error_text = PARSE_ERROR_MSG

        return error_text

    def _process_empty_reponse(self, response, action_result):
        if response.status_code == 200 or response.status_code == 204:
            return RetVal(phantom.APP_SUCCESS, {})
        return RetVal(action_result.set_status(phantom.APP_ERROR, "Empty response and no information in the header"), None)

    def _process_html_response(self, response, action_result):

        # An html response, treat it like an error
        status_code = response.status_code

        try:
            soup = BeautifulSoup(response.text, "html.parser")
            error_text = soup.text
            split_lines = error_text.split('\n')
            split_lines = [x.strip() for x in split_lines if x.strip()]
            error_text = '\n'.join(split_lines)
        except:
            error_text = "Cannot parse error details"

        # Handling of error_text for both the Python 2 and Python 3 versions
        error_text = self._handle_py_ver_compat_for_input_str(error_text)

        message = "Please check the asset configuration parameters (the base_url should not end with /api/v1 e.g. https://admin.zscaler_instance.net)."

        if len(error_text) <= 500:
            message += "Status Code: {0}. Data from server:\n{1}\n".format(status_code, error_text)

        message = message.replace('{', '{{').replace('}', '}}')
        return RetVal(action_result.set_status(phantom.APP_ERROR, message), None)

    def _process_json_response(self, r, action_result):

        # Try a json parse
        try:
            resp_json = r.json()
        except Exception as e:
            return RetVal(action_result.set_status(phantom.APP_ERROR, "Unable to parse JSON response. Error: {0}".format(self._get_error_message_from_exception(e))), None)

        # Please specify the status codes here
        if 200 <= r.status_code < 399:
            return RetVal(phantom.APP_SUCCESS, resp_json)

        # You should process the error returned in the json
        try:
            message = resp_json['message']
        except:
            message = "Error from server. Status Code: {0} Data from server: {1}".format(
                r.status_code, r.text.replace('{', '{{').replace('}', '}}')
            )
        return RetVal(action_result.set_status(phantom.APP_ERROR, message), None)

    def _process_response(self, r, action_result):

        # store the r_text in debug data, it will get dumped in the logs if the action fails
        if hasattr(action_result, 'add_debug_data'):
            action_result.add_debug_data({'r_status_code': r.status_code})
            action_result.add_debug_data({'r_text': r.text})
            action_result.add_debug_data({'r_headers': r.headers})

        # Process each 'Content-Type' of response separately

        # Process a json response
        if 'json' in r.headers.get('Content-Type', ''):
            return self._process_json_response(r, action_result)

        # Process an HTML resonse, Do this no matter what the api talks.
        # There is a high chance of a PROXY in between phantom and the rest of
        # world, in case of errors, PROXY's return HTML, this function parses
        # the error and adds it to the action_result.
        if 'html' in r.headers.get('Content-Type', ''):
            return self._process_html_response(r, action_result)

        # it's not content-type that is to be parsed, handle an empty response
        if not r.text:
            return self._process_empty_reponse(r, action_result)

        # everything else is actually an error at this point
        message = "Can't process response from server. Status Code: {0} Data from server: {1}".format(
            r.status_code, r.text.replace('{', '{{').replace('}', '}}')
        )

        return RetVal(action_result.set_status(phantom.APP_ERROR, message), None)

    def _is_ip(self, input_ip_address):
        """ Function that checks given address and return True if address is valid IPv4 or IPV6 address.

        :param input_ip_address: IP address
        :return: status (success/failure)
        """

        ip_address_input = input_ip_address

        try:
            try:
                ipaddress.ip_address(unicode(ip_address_input))
            except NameError:
                ipaddress.ip_address(str(ip_address_input))
        except:
            return False

        return True

    def _make_rest_call(self, endpoint, action_result, headers=None, params=None, data=None, method="get"):

        resp_json = None

        if headers is None:
            headers = {}

        headers.update(self._headers)

        try:
            request_func = getattr(requests, method)
        except AttributeError:
            return RetVal(action_result.set_status(phantom.APP_ERROR, "Invalid method: {0}".format(method)), resp_json)

        # Create a URL to connect to
        url = '{}{}'.format(self._handle_py_ver_compat_for_input_str(self._base_url), endpoint)

        try:
            r = request_func(
                url,
                json=data,
                headers=headers,
                params=params
            )
        except Exception as e:
            return RetVal(action_result.set_status( phantom.APP_ERROR, "Error Connecting to Zscaler server. {}"
                    .format(self._get_error_message_from_exception(e))), resp_json)

        self._response = r

        return self._process_response(r, action_result)

    def _parse_retry_time(self, retry_time):
        # Instead of just giving a second value, "retry-time" will return a string like "0 seconds"
        # I don't know if the second unit can be not seconds
        parts = retry_time.split()
        if parts[1].lower() == "seconds":
            return int(parts[0])
        if parts[1].lower() == "minutes":
            return int(parts[0]) * 60
        else:
            return None

    def _make_rest_call_helper(self, *args, **kwargs):
        # There are two rate limits
        #  1. There is a maximum limt of requests per second, depending on if its a GET / POST / PUT / DETE
        #  2. There is a maximum number of requests per hour
        # Regardless, the response will include a try-after value, which we can use to sleep
        ret_val, response = self._make_rest_call(*args, **kwargs)
        if phantom.is_fail(ret_val):
            if self._response is None:
                return ret_val, response
            if self._response.status_code == 409:  # Lock not available
                # This basically just means we need to try again
                self.debug_print("Error 409: Lock not available")
                self.send_progress("Error 409: Lock not available: Retrying in 1 second")
                time.sleep(1)
                return self._make_rest_call_helper(*args, **kwargs)
            if self._response.status_code == 429:  # Rate limit exceeded
                try:
                    retry_time = self._response.json()['Retry-After']
                except KeyError:
                    self.debug_print("KeyError")
                    return ret_val, response
                self.debug_print("Retry Time: {}".format(retry_time))
                seconds_to_wait = self._parse_retry_time(retry_time)
                if seconds_to_wait is None or seconds_to_wait < 0:
                    return retry_time, response
                self.send_progress("Exceeded rate limit: Retrying after {}".format(retry_time))
                time.sleep(seconds_to_wait)
                return self._make_rest_call_helper(*args, **kwargs)
        return ret_val, response

    def _obfuscate_api_key(self, api_key):
        now = str(int(time.time() * 1000))
        n = now[-6:]
        r = str(int(n) >> 1).zfill(6)
        key = ""
        for i in range(0, len(n), 1):
            key += api_key[int(n[i])]
        for j in range(0, len(r), 1):
            key += api_key[int(r[j]) + 2]

        return now, key

    def _init_session(self):
        config = self.get_config()
        username = config['username']
        password = config['password']
        api_key = config['api_key']
        try:
            timestamp, obf_api_key = self._obfuscate_api_key(api_key)
        except:
            return self.set_status(
                phantom.APP_ERROR,
                "Error obfuscating API key"
            )

        body = {
            'apiKey': obf_api_key,
            'username': username,
            'password': password,
            'timestamp': timestamp
        }

        action_result = ActionResult()
        ret_val, response = self._make_rest_call_helper(
            '/api/v1/authenticatedSession',
            action_result, data=body,
            method='post'
        )
        if phantom.is_fail(ret_val):
            self.debug_print('Error starting Zscaler session: {}'.format(action_result.get_message()))
            return self.set_status(
                phantom.APP_ERROR,
                'Error starting Zscaler session: {}'.format(action_result.get_message())
            )
        else:
            self.save_progress('Successfully started Zscaler session')
            self._headers = {
                'cookie': self._response.headers['Set-Cookie'].split(';')[0].strip()
            }
            return phantom.APP_SUCCESS

    def _deinit_session(self):
        action_result = ActionResult()
        ret_val, response = self._make_rest_call_helper('/api/v1/authenticatedSession', action_result, method='delete')

        if phantom.is_fail(ret_val):
            self.debug_print("Deleting the authenticated session failed on the ZScaler server.")
            self.debug_print("Marking the action as successful run.")

        return phantom.APP_SUCCESS

    def _handle_test_connectivity(self, param):
        # If we are here we have successfully initialized a session
        self.save_progress("Test Connectivity Passed")
        return self.set_status(phantom.APP_SUCCESS)

    def _filter_endpoints(self, action_result, to_add, existing, action, name):
        if action == "REMOVE_FROM_LIST":
            msg = "{} contains none of these endpoints".format(name)
            endpoints = list(set(existing) - (set(existing) - set(to_add)))
        else:
            msg = "{} contains all of these endpoints".format(name)
            endpoints = list(set(to_add) - set(existing))

        if not endpoints:
            summary = action_result.set_summary({})
            summary['updated'] = []
            summary['ignored'] = to_add
            return RetVal(action_result.set_status(phantom.APP_SUCCESS, msg), None)
        return RetVal(phantom.APP_SUCCESS, endpoints)

    def _get_blocklist(self, action_result):
        return self._make_rest_call_helper('/api/v1/security/advanced', action_result)

    def _check_blocklist(self, action_result, endpoints, action):
        ret_val, response = self._get_blocklist(action_result)
        if phantom.is_fail(ret_val):
            return RetVal(ret_val, None)

        blocklist = response.get('blacklistUrls', [])

        return self._filter_endpoints(action_result, endpoints, blocklist, action, 'Blocklist')

    def _amend_blocklist(self, action_result, endpoints, action):
        ret_val, filtered_endpoints = self._check_blocklist(action_result, endpoints, action)
        if phantom.is_fail(ret_val) or filtered_endpoints is None:
            return ret_val

        params = {'action': action}
        data = {
            "blacklistUrls": filtered_endpoints
        }
        ret_val, response = self._make_rest_call_helper(
            '/api/v1/security/advanced/blacklistUrls', action_result, params=params,
            data=data, method="post"
        )
        if phantom.is_fail(ret_val) and self._response.status_code != 204:
            return ret_val
        summary = action_result.set_summary({})
        summary['updated'] = filtered_endpoints
        summary['ignored'] = list(set(endpoints) - set(filtered_endpoints))
        # Encode the unicode IP or URL strings
        summary['updated'] = [self._handle_py_ver_compat_for_input_str(element) for element in summary['updated']]
        summary['ignored'] = [self._handle_py_ver_compat_for_input_str(element) for element in summary['ignored']]
        return action_result.set_status(phantom.APP_SUCCESS)

    def _get_allowlist(self, action_result):
        return self._make_rest_call_helper('/api/v1/security', action_result)

    def _check_allowlist(self, action_result, endpoints, action):
        ret_val, response = self._get_allowlist(action_result)
        if phantom.is_fail(ret_val):
            return RetVal(ret_val, None)

        allowlist = response.get('whitelistUrls', [])
        self._allowlist = allowlist

        return self._filter_endpoints(action_result, endpoints, allowlist, action, 'Allowlist')

    def _amend_allowlist(self, action_result, endpoints, action):
        ret_val, filtered_endpoints = self._check_allowlist(action_result, endpoints, action)
        if phantom.is_fail(ret_val) or filtered_endpoints is None:
            return ret_val

        if action == "ADD_TO_LIST":
            to_add_endpoints = list(set(self._allowlist + filtered_endpoints))
        else:
            to_add_endpoints = list(set(self._allowlist) - set(filtered_endpoints))

        data = {
            "whitelistUrls": to_add_endpoints
        }
        ret_val, response = self._make_rest_call_helper(
            '/api/v1/security', action_result,
            data=data, method='put'
        )
        if phantom.is_fail(ret_val):
            return ret_val

        action_result.add_data(response)
        summary = action_result.set_summary({})
        summary['updated'] = filtered_endpoints
        summary['ignored'] = list(set(endpoints) - set(filtered_endpoints))
        # Encode the unicode IP or URL strings
        summary['updated'] = [self._handle_py_ver_compat_for_input_str(element) for element in summary['updated']]
        summary['ignored'] = [self._handle_py_ver_compat_for_input_str(element) for element in summary['ignored']]
        return action_result.set_status(phantom.APP_SUCCESS)

    def _get_category(self, action_result, category):
        ret_val, response = self._make_rest_call_helper('/api/v1/urlCategories', action_result)
        if phantom.is_fail(ret_val):
            return ret_val, response

        for cat in response:
            if cat.get('configuredName', None) == category:
                return RetVal(phantom.APP_SUCCESS, cat)

        for cat in response:
            if cat['id'] == category:
                return RetVal(phantom.APP_SUCCESS, cat)

        return RetVal(
            action_result.set_status(
                phantom.APP_ERROR, "Unable to find category"
            ),
            None
        )

    def _check_category(self, action_result, endpoints, category, action):
        ret_val, response = self._get_category(action_result, category)
        if phantom.is_fail(ret_val):
            return ret_val, response

        self._category = response
        urls = response.get('dbCategorizedUrls', [])

        return self._filter_endpoints(action_result, endpoints, urls, action, 'Category')

    def _amend_category(self, action_result, endpoints, category, action):
        ret_val, filtered_endpoints = self._check_category(action_result, endpoints, category, action)
        if phantom.is_fail(ret_val) or filtered_endpoints is None:
            return ret_val

        data = self._category

        if action == "ADD_TO_LIST":
            to_add_endpoints = list(set(data.get('dbCategorizedUrls', []) + filtered_endpoints))
        else:
            to_add_endpoints = list(set(data.get('dbCategorizedUrls', [])) - set(filtered_endpoints))

        data['dbCategorizedUrls'] = to_add_endpoints
        ret_val, response = self._make_rest_call_helper(
            '/api/v1/urlCategories/{}'.format(self._category['id']),
            action_result, data=data, method='put'
        )
        if phantom.is_fail(ret_val):
            return ret_val
        action_result.add_data(response)
        summary = action_result.set_summary({})
        summary['updated'] = filtered_endpoints
        summary['ignored'] = list(set(endpoints) - set(filtered_endpoints))
        # Encode the unicode IP or URL strings
        summary['updated'] = [self._handle_py_ver_compat_for_input_str(element) for element in summary['updated']]
        summary['ignored'] = [self._handle_py_ver_compat_for_input_str(element) for element in summary['ignored']]
        return action_result.set_status(phantom.APP_SUCCESS)

    def _block_endpoint(self, action_result, endpoints, category):
        list_endpoints = list()
        list_endpoints = [self._handle_py_ver_compat_for_input_str(x.strip()) for x in endpoints.split(',')]
        endpoints = list(filter(None, list_endpoints))
        endpoints = self._truncate_protocol(endpoints)

        if self.get_action_identifier() in ['block_url']:
            ret_val = self._check_for_overlength(action_result, endpoints)
            if phantom.is_fail(ret_val):
                return ret_val

        if category is None:
            return self._amend_blocklist(action_result, endpoints, 'ADD_TO_LIST')
        else:
            return self._amend_category(action_result, endpoints, category, 'ADD_TO_LIST')

    def _unblock_endpoint(self, action_result, endpoints, category):
        list_endpoints = list()
        list_endpoints = [self._handle_py_ver_compat_for_input_str(x.strip()) for x in endpoints.split(',')]
        endpoints = list(filter(None, list_endpoints))
        endpoints = self._truncate_protocol(endpoints)

        if self.get_action_identifier() in ['unblock_url']:
            ret_val = self._check_for_overlength(action_result, endpoints)
            if phantom.is_fail(ret_val):
                return ret_val

        if category is None:
            return self._amend_blocklist(action_result, endpoints, 'REMOVE_FROM_LIST')
        else:
            return self._amend_category(action_result, endpoints, category, 'REMOVE_FROM_LIST')

    def _handle_block_ip(self, param):
        action_result = self.add_action_result(ActionResult(dict(param)))
        return self._block_endpoint(action_result, param['ip'], param.get('url_category'))

    def _handle_block_url(self, param):
        action_result = self.add_action_result(ActionResult(dict(param)))
        return self._block_endpoint(action_result, param['url'], param.get('url_category'))

    def _handle_unblock_ip(self, param):
        action_result = self.add_action_result(ActionResult(dict(param)))
        return self._unblock_endpoint(action_result, param['ip'], param.get('url_category'))

    def _handle_unblock_url(self, param):
        action_result = self.add_action_result(ActionResult(dict(param)))
        return self._unblock_endpoint(action_result, param['url'], param.get('url_category'))

    def _allowlist_endpoint(self, action_result, endpoints, category):
        list_endpoints = list()
        list_endpoints = [self._handle_py_ver_compat_for_input_str(x.strip()) for x in endpoints.split(',')]
        endpoints = list(filter(None, list_endpoints))
        endpoints = self._truncate_protocol(endpoints)

        if self.get_action_identifier() in ['allow_url']:
            ret_val = self._check_for_overlength(action_result, endpoints)
            if phantom.is_fail(ret_val):
                return ret_val

        if category is None:
            return self._amend_allowlist(action_result, endpoints, 'ADD_TO_LIST')
        else:
            return self._amend_category(action_result, endpoints, category, 'ADD_TO_LIST')

    def _unallow_endpoint(self, action_result, endpoints, category):
        list_endpoints = list()
        list_endpoints = [self._handle_py_ver_compat_for_input_str(x.strip()) for x in endpoints.split(',')]
        endpoints = list(filter(None, list_endpoints))
        endpoints = self._truncate_protocol(endpoints)

        if self.get_action_identifier() in ['unallow_url']:
            ret_val = self._check_for_overlength(action_result, endpoints)
            if phantom.is_fail(ret_val):
                return ret_val

        if category is None:
            return self._amend_allowlist(action_result, endpoints, 'REMOVE_FROM_LIST')
        else:
            return self._amend_category(action_result, endpoints, category, 'REMOVE_FROM_LIST')

    def _handle_allow_ip(self, param):
        action_result = self.add_action_result(ActionResult(dict(param)))
        return self._allowlist_endpoint(action_result, param['ip'], param.get('url_category'))

    def _handle_allow_url(self, param):
        action_result = self.add_action_result(ActionResult(dict(param)))
        return self._allowlist_endpoint(action_result, param['url'], param.get('url_category'))

    def _handle_unallow_ip(self, param):
        action_result = self.add_action_result(ActionResult(dict(param)))
        return self._unallow_endpoint(action_result, param['ip'], param.get('url_category'))

    def _handle_unallow_url(self, param):
        action_result = self.add_action_result(ActionResult(dict(param)))
        return self._unallow_endpoint(action_result, param['url'], param.get('url_category'))

    def _lookup_endpoint(self, action_result, endpoints):

        if not endpoints:
            action_result.set_status(phantom.APP_ERROR, "Please provide valid list of URL(s)")

        ret_val, response = self._make_rest_call_helper(
            '/api/v1/urlLookup', action_result,
            data=endpoints, method='post'
        )
        if phantom.is_fail(ret_val):
            return ret_val

        ret_val, blocklist_response = self._make_rest_call_helper(
            '/api/v1/security/advanced', action_result,
            data=endpoints, method='get'
        )

        if phantom.is_fail(ret_val):
            return ret_val

        for e in endpoints:
            if e in blocklist_response.get('blacklistUrls', []):
                [response[i].update({"blocklisted": True}) for i, item in enumerate(response) if item['url'] == e]
            else:
                [response[i].update({"blocklisted": False}) for i, item in enumerate(response) if item['url'] == e]

        action_result.update_data(response)

        return action_result.set_status(phantom.APP_SUCCESS, "Successfully completed lookup")

    def _handle_get_report(self, param):
        """
        This action is used to retrieve a sandbox report of provided md5 file hash
        :param file_hash: md5Hash of file
        :return: status phantom.APP_ERROR/phantom.APP_SUCCESS(along with appropriate message)
        """

        action_result = self.add_action_result(ActionResult(dict(param)))

        file_hash = param['file_hash']

        ret_val, sandbox_report = self._make_rest_call_helper('/api/v1/sandbox/report/{0}?details=full'.format(file_hash), action_result)

        if phantom.is_fail(ret_val):
            return action_result.get_status()

        if sandbox_report.get(ZSCALER_JSON_FULL_DETAILS) and ZSCLAER_ERR_MD5_UNKNOWN_MSG in sandbox_report.get(
                                                                        ZSCALER_JSON_FULL_DETAILS):
            return action_result.set_status(phantom.APP_ERROR, sandbox_report.get(ZSCALER_JSON_FULL_DETAILS))

        action_result.add_data(sandbox_report)

        return action_result.set_status(phantom.APP_SUCCESS, ZSCALER_SANDBOX_GET_REPORT_MSG)

    def _handle_list_url_categories(self, param):
        """
        This action is used to fetch all the URL categories
        :param: No parameters
        :return: status phantom.APP_ERROR/phantom.APP_SUCCESS(along with appropriate message)
        """

        action_result = self.add_action_result(ActionResult(dict(param)))
        ret_val, list_url_categories = self._make_rest_call_helper('/api/v1/urlCategories', action_result)

        if phantom.is_fail(ret_val):
            return action_result.get_status()

        for url_category in list_url_categories:
            action_result.add_data(url_category)

        summary = action_result.update_summary({})
        summary['total_url_categories'] = action_result.get_data_size()

        return action_result.set_status(phantom.APP_SUCCESS)

    def _handle_lookup_ip(self, param):
        action_result = self.add_action_result(ActionResult(dict(param)))

        list_endpoints = list()
        list_endpoints = [self._handle_py_ver_compat_for_input_str(x.strip()) for x in param['ip'].split(',')]
        endpoints = list(filter(None, list_endpoints))

        return self._lookup_endpoint(action_result, endpoints)

    def _handle_lookup_url(self, param):

        action_result = self.add_action_result(ActionResult(dict(param)))

        list_endpoints = list()
        list_endpoints = [self._handle_py_ver_compat_for_input_str(x.strip()) for x in param['url'].split(',')]
        endpoints = list(filter(None, list_endpoints))

        endpoints = self._truncate_protocol(endpoints)
        ret_val = self._check_for_overlength(action_result, endpoints)

        if phantom.is_fail(ret_val):
            return ret_val

        return self._lookup_endpoint(action_result, endpoints)

    def _truncate_protocol(self, endpoints):
        """
        This function truncates the protocol from the list of URLs if present
        :param: endpoints: list of URLs
        :return: updated list of url
        """
        for i in range(len(endpoints)):
            if endpoints[i].startswith("http://"):
                endpoints[i] = endpoints[i][(len("http://")):]
            elif endpoints[i].startswith("https://"):
                endpoints[i] = endpoints[i][(len("https://")):]

        return endpoints

    def _check_for_overlength(self, action_result, endpoints):
        """This function checks whether the length of each url is not more
        than 1024
        :param: :endpoints: list of URLs
        """
        for url in endpoints:
            if len(url) > 1024:
                return action_result.set_status(phantom.APP_ERROR,
                        "Please provide valid comma-separated values in the action parameter. Max allowed length for each value is 1024.")
        return phantom.APP_SUCCESS

    def handle_action(self, param):

        ret_val = phantom.APP_SUCCESS

        # Get the action that we are supposed to execute for this App Run
        action_id = self.get_action_identifier()

        self.debug_print("action_id", self.get_action_identifier())

        if action_id == 'test_connectivity':
            ret_val = self._handle_test_connectivity(param)

        elif action_id == 'list_url_categories':
            ret_val = self._handle_list_url_categories(param)

        elif action_id == 'get_report':
            ret_val = self._handle_get_report(param)

        elif action_id == 'block_ip':
            ret_val = self._handle_block_ip(param)

        elif action_id == 'block_url':
            ret_val = self._handle_block_url(param)

        elif action_id == 'unblock_ip':
            ret_val = self._handle_unblock_ip(param)

        elif action_id == 'unblock_url':
            ret_val = self._handle_unblock_url(param)

        elif action_id == 'allow_ip':
            ret_val = self._handle_allow_ip(param)

        elif action_id == 'allow_url':
            ret_val = self._handle_allow_url(param)

        elif action_id == 'unallow_ip':
            ret_val = self._handle_unallow_ip(param)

        elif action_id == 'unallow_url':
            ret_val = self._handle_unallow_url(param)

        elif action_id == "lookup_ip":
            ret_val = self._handle_lookup_ip(param)

        elif action_id == 'lookup_url':
            ret_val = self._handle_lookup_url(param)

        return ret_val

    def initialize(self):

        # Fetching the Python major version
        try:
            self._python_version = int(sys.version_info[0])
        except:
            return self.set_status(phantom.APP_ERROR, "Error occurred while getting the Phantom server's Python major version.")

        # Load the state in initialize, use it to store data
        # that needs to be accessed across actions
        self._state = self.load_state()
        config = self.get_config()
        self._base_url = config['base_url'].rstrip('/')
        self._username = config['username']
        self._password = config['password']
        self._headers = {}

        self.set_validator('ipv6', self._is_ip)

        return self._init_session()

    def finalize(self):

        self.save_state(self._state)
        return self._deinit_session()


if __name__ == '__main__':

    import pudb
    import argparse
    pudb.set_trace()

    argparser = argparse.ArgumentParser()

    argparser.add_argument('input_test_json', help='Input Test JSON file')
    argparser.add_argument('-u', '--username', help='username', required=False)
    argparser.add_argument('-p', '--password', help='password', required=False)

    args = argparser.parse_args()
    session_id = None

    if (args.username and args.password):
        login_url = BaseConnector._get_phantom_base_url() + "login"
        try:
            print("Accessing the Login page")
            r = requests.get(login_url, verify=False)
            csrftoken = r.cookies['csrftoken']
            data = {'username': args.username, 'password': args.password, 'csrfmiddlewaretoken': csrftoken}
            headers = {'Cookie': 'csrftoken={0}'.format(csrftoken), 'Referer': login_url}

            print("Logging into Platform to get the session id")
            r2 = requests.post(login_url, verify=False, data=data, headers=headers)
            session_id = r2.cookies['sessionid']

        except Exception as e:
            print(("Unable to get session id from the platform. Error: {0}".format(str(e))))
            exit(1)

    if (len(sys.argv) < 2):
        print("No test json specified as input")
        exit(0)

    with open(args.input_test_json) as f:
        in_json = f.read()
        in_json = json.loads(in_json)
        print(json.dumps(in_json, indent=4))

        connector = ZscalerConnector()
        connector.print_progress_message = True

        if (session_id is not None):
            in_json['user_session_token'] = session_id

        ret_val = connector._handle_action(json.dumps(in_json), None)
        print(json.dumps(json.loads(ret_val), indent=4))

    exit(0)
