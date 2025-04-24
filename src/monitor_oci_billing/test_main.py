#!/usr/bin/env python3
"""
OCI 账单权限用户创建工具的单元测试
"""

import unittest
from unittest.mock import patch, MagicMock
import os
import sys

# 添加项目根目录到Python路径
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from main import (
    setup_logging,
    get_oci_config,
    create_identity_client,
    create_user,
    create_billing_policy,
    main
)


class TestOCIBillingUser(unittest.TestCase):
    """测试OCI账单权限用户创建功能"""

    @patch('main.logging')
    def test_setup_logging(self, mock_logging):
        """测试日志设置功能"""
        mock_logger = MagicMock()
        mock_logging.getLogger.return_value = mock_logger
        
        result = setup_logging()
        
        mock_logging.getLogger.assert_called_once_with('oci_billing_user')
        mock_logger.setLevel.assert_called_once()
        self.assertEqual(result, mock_logger)

    @patch('main.oci.config.from_file')
    def test_get_oci_config(self, mock_from_file):
        """测试获取OCI配置功能"""
        mock_config = {'tenancy': 'test_tenancy', 'user': 'test_user'}
        mock_from_file.return_value = mock_config
        
        # 测试默认参数
        result = get_oci_config()
        mock_from_file.assert_called_with(
            file_location=os.path.expanduser("~/.oci/config"), 
            profile_name=None
        )
        self.assertEqual(result, mock_config)
        
        # 测试自定义参数
        result = get_oci_config('/custom/path', 'CUSTOM')
        mock_from_file.assert_called_with(
            file_location='/custom/path', 
            profile_name='CUSTOM'
        )

    @patch('main.IdentityClient')
    def test_create_identity_client(self, mock_identity_client):
        """测试创建身份客户端功能"""
        mock_config = {'tenancy': 'test_tenancy'}
        mock_client = MagicMock()
        mock_identity_client.return_value = mock_client
        
        result = create_identity_client(mock_config)
        
        mock_identity_client.assert_called_once_with(mock_config)
        self.assertEqual(result, mock_client)

    def test_create_user(self):
        """测试创建用户功能"""
        mock_identity_client = MagicMock()
        mock_response = MagicMock()
        mock_response.data.id = 'test_user_ocid'
        mock_identity_client.create_user.return_value = mock_response
        
        result = create_user(
            mock_identity_client, 
            'test_compartment_id', 
            'test_user_name', 
            'test_description'
        )
        
        mock_identity_client.create_user.assert_called_once()
        self.assertEqual(result, 'test_user_ocid')

    def test_create_billing_policy(self):
        """测试创建账单策略功能"""
        mock_identity_client = MagicMock()
        mock_response = MagicMock()
        mock_response.data.id = 'test_policy_ocid'
        mock_identity_client.create_policy.return_value = mock_response
        
        result = create_billing_policy(
            mock_identity_client, 
            'test_compartment_id', 
            'test_user_ocid', 
            'test_policy_name'
        )
        
        mock_identity_client.create_policy.assert_called_once()
        self.assertEqual(result, 'test_policy_ocid')

    @patch('main.setup_logging')
    @patch('main.get_oci_config')
    @patch('main.create_identity_client')
    @patch('main.create_user')
    @patch('main.create_billing_policy')
    @patch('main.argparse.ArgumentParser')
    def test_main(self, mock_parser, mock_create_billing_policy, 
                 mock_create_user, mock_create_identity_client, 
                 mock_get_oci_config, mock_setup_logging):
        """测试主函数功能"""
        # 设置模拟对象
        mock_logger = MagicMock()
        mock_setup_logging.return_value = mock_logger
        
        mock_args = MagicMock()
        mock_args.config = None
        mock_args.profile = 'DEFAULT'
        mock_args.user_name = 'test_user'
        mock_args.user_description = 'test description'
        mock_args.policy_name = None
        
        mock_parser_instance = MagicMock()
        mock_parser_instance.parse_args.return_value = mock_args
        mock_parser.return_value = mock_parser_instance
        
        mock_config = {'tenancy': 'test_tenancy'}
        mock_get_oci_config.return_value = mock_config
        
        mock_identity_client = MagicMock()
        mock_create_identity_client.return_value = mock_identity_client
        
        mock_create_user.return_value = 'test_user_ocid'
        mock_create_billing_policy.return_value = 'test_policy_ocid'
        
        # 执行测试
        main()
        
        # 验证调用
        mock_setup_logging.assert_called_once()
        mock_get_oci_config.assert_called_once()
        mock_create_identity_client.assert_called_once_with(mock_config)
        mock_create_user.assert_called_once()
        mock_create_billing_policy.assert_called_once()
        mock_logger.info.assert_called()


if __name__ == '__main__':
    unittest.main()