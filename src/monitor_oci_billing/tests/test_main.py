import unittest
import configparser
import os
from unittest.mock import patch, MagicMock, mock_open
# Assuming main.py is in the parent directory relative to tests/
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
import main as billing_monitor # Import the main script

# Define the path to the test config relative to this test file
TEST_CONFIG_DIR = os.path.dirname(__file__)
TEST_CONFIG_PATH = os.path.join(TEST_CONFIG_DIR, 'test_config.ini')

class TestConfigLoading(unittest.TestCase):

    def test_load_config_success(self):
        """Tests successful loading of a valid config file."""
        config_content = """
[OCI]
config_file = ~/.oci/config
profile_name = DEFAULT
tenancy_ocid = ocid1.tenancy.oc1..xxx

[Billing]
start_time = 2024-01-01T00:00:00Z
cost_threshold = 100.00
currency = USD

[Alerting]
method = log
"""
        # Use mock_open to simulate the config file existing
        with patch('builtins.open', mock_open(read_data=config_content)) as mocked_file:
            with patch('os.path.exists') as mock_exists:
                mock_exists.return_value = True # Simulate file exists
                config = billing_monitor.load_config('dummy_path.ini')
                self.assertIsInstance(config, configparser.ConfigParser)
                self.assertEqual(config.get('OCI', 'tenancy_ocid'), 'ocid1.tenancy.oc1..xxx')
                self.assertEqual(config.getfloat('Billing', 'cost_threshold'), 100.00)
                self.assertEqual(config.get('Alerting', 'method'), 'log')
                mocked_file.assert_called_once_with('dummy_path.ini') # Check open was called
                mock_exists.assert_called_once_with('dummy_path.ini') # Check exists was called

    def test_load_config_file_not_found(self):
        """Tests FileNotFoundError when config file doesn't exist."""
        with patch('os.path.exists') as mock_exists:
            mock_exists.return_value = False # Simulate file does not exist
            with self.assertRaises(FileNotFoundError):
                billing_monitor.load_config('non_existent_config.ini')
            mock_exists.assert_called_once_with('non_existent_config.ini')

    def test_load_config_missing_section(self):
        """Tests error handling for missing sections."""
        config_content = """
[OCI]
tenancy_ocid = ocid1.tenancy.oc1..xxx
# Missing [Billing] section
"""
        with patch('builtins.open', mock_open(read_data=config_content)):
             with patch('os.path.exists') as mock_exists:
                mock_exists.return_value = True
                with self.assertRaises(configparser.Error):
                    billing_monitor.load_config('dummy_path.ini')

    def test_load_config_missing_option(self):
        """Tests error handling for missing options."""
        config_content = """
[OCI]
tenancy_ocid = ocid1.tenancy.oc1..xxx
[Billing]
# Missing start_time
cost_threshold = 100.00
currency = USD
"""
        with patch('builtins.open', mock_open(read_data=config_content)):
            with patch('os.path.exists') as mock_exists:
                mock_exists.return_value = True
                with self.assertRaises(configparser.Error):
                    billing_monitor.load_config('dummy_path.ini')


class TestAlerting(unittest.TestCase):

    @patch('main.logger') # Mock the logger inside the main module
    def test_trigger_alert_log(self, mock_logger):
        """Tests that the 'log' alerting method logs a warning."""
        config = configparser.ConfigParser() # Dummy config for this test
        message = "Test alert message"
        billing_monitor.trigger_alert('log', message, config)
        # Check if logger.warning was called with the expected message format
        mock_logger.warning.assert_any_call(f"ALERT TRIGGERED: {message}")

    @patch('main.logger')
    def test_trigger_alert_unsupported(self, mock_logger):
        """Tests handling of unsupported alert methods."""
        config = configparser.ConfigParser()
        message = "Test alert message"
        billing_monitor.trigger_alert('sms', message, config)
        mock_logger.warning.assert_any_call(f"ALERT TRIGGERED: {message}") # Should still log the trigger
        mock_logger.error.assert_called_with("Unsupported alerting method configured: sms")

# --- Mock OCI Data Tests (More complex, requires deeper mocking) ---
# These would typically mock the oci.usage_api.UsageapiClient and its methods

class TestOCIMocking(unittest.TestCase):

    # Example of how you might start mocking OCI calls
    @patch('main.oci') # Patch the entire oci module used in main
    @patch('main.load_config') # Also mock config loading for simplicity
    @patch('main.logger')
    def test_run_check_below_threshold(self, mock_logger, mock_load_config, mock_oci):
        """Tests the main check logic when cost is below threshold."""

        # 1. Setup Mock Config
        mock_config = configparser.ConfigParser()
        mock_config['OCI'] = {'tenancy_ocid': 'ocid.test', 'config_file': 'dummy', 'profile_name': 'DEFAULT'}
        mock_config['Billing'] = {'start_time': '2024-07-01T00:00:00Z', 'cost_threshold': '100.0', 'currency': 'USD'}
        mock_config['Alerting'] = {'method': 'log'}
        mock_load_config.return_value = mock_config

        # 2. Setup Mock OCI SDK Config Function (called within run_check)
        mock_oci.config.from_file.return_value = {'user': 'fake_user', 'key_file': 'fake.pem', 'fingerprint': 'xx:xx', 'tenancy': 'ocid.test', 'region': 'us-ashburn-1'}

        # 3. Setup Mock Usage API Client and its response
        mock_usage_client = MagicMock()
        mock_usage_response = MagicMock()
        mock_usage_item = MagicMock()
        mock_usage_item.computed_amount = 50.50
        mock_usage_item.currency.iso_code = 'USD' # Mock the nested attribute
        mock_usage_response.data.items = [mock_usage_item]
        mock_usage_client.request_summarized_usages.return_value = mock_usage_response
        mock_oci.usage_api.UsageapiClient.return_value = mock_usage_client # Make the client constructor return our mock

        # 4. Run the check
        billing_monitor.run_check('dummy_config_path')

        # 5. Assertions
        mock_load_config.assert_called_once_with('dummy_config_path')
        mock_oci.usage_api.UsageapiClient.assert_called_once() # Check client was created
        mock_usage_client.request_summarized_usages.assert_called_once() # Check API was called
        mock_logger.info.assert_any_call("Cumulative cost since 2024-07-01T00:00:00Z: 50.50 USD")
        mock_logger.info.assert_any_call("Cumulative cost is within the threshold (100.00 USD).")
        mock_logger.warning.assert_not_called() # No alert should be triggered

    @patch('main.oci')
    @patch('main.load_config')
    @patch('main.logger')
    @patch('main.trigger_alert') # Mock the alert function directly
    def test_run_check_above_threshold(self, mock_trigger_alert, mock_logger, mock_load_config, mock_oci):
        """Tests the main check logic when cost is above threshold."""
        # 1. Setup Mock Config
        mock_config = configparser.ConfigParser()
        mock_config['OCI'] = {'tenancy_ocid': 'ocid.test', 'config_file': 'dummy', 'profile_name': 'DEFAULT'}
        mock_config['Billing'] = {'start_time': '2024-07-01T00:00:00Z', 'cost_threshold': '100.0', 'currency': 'USD'}
        mock_config['Alerting'] = {'method': 'log'}
        mock_load_config.return_value = mock_config

        # 2. Setup Mock OCI SDK Config
        mock_oci.config.from_file.return_value = {'user': 'fake_user', 'key_file': 'fake.pem', 'fingerprint': 'xx:xx', 'tenancy': 'ocid.test', 'region': 'us-ashburn-1'}

        # 3. Setup Mock Usage API Client and response (cost > threshold)
        mock_usage_client = MagicMock()
        mock_usage_response = MagicMock()
        mock_usage_item1 = MagicMock()
        mock_usage_item1.computed_amount = 75.25
        mock_usage_item1.currency.iso_code = 'USD'
        mock_usage_item2 = MagicMock()
        mock_usage_item2.computed_amount = 30.00
        mock_usage_item2.currency.iso_code = 'USD'
        mock_usage_response.data.items = [mock_usage_item1, mock_usage_item2] # Total 105.25
        mock_usage_client.request_summarized_usages.return_value = mock_usage_response
        mock_oci.usage_api.UsageapiClient.return_value = mock_usage_client

        # 4. Run the check
        billing_monitor.run_check('dummy_config_path')

        # 5. Assertions
        mock_logger.info.assert_any_call("Cumulative cost since 2024-07-01T00:00:00Z: 105.25 USD")
        # Check that trigger_alert was called correctly
        expected_message = "OCI cumulative cost 105.25 USD has exceeded the threshold of 100.00 USD since 2024-07-01T00:00:00Z."
        mock_trigger_alert.assert_called_once_with('log', expected_message, mock_config)


if __name__ == '__main__':
    unittest.main()