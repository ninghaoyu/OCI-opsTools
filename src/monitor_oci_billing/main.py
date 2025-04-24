#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import oci
import configparser
import logging
import os
import sys
import argparse
from datetime import datetime, timezone
import schedule
import time
from typing import Optional, Dict, Any, NoReturn
import requests
import json

# --- Logging Configuration ---
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- Constants ---
DEFAULT_CONFIG_PATH = os.path.join(os.path.dirname(__file__), 'config.ini')
DEFAULT_INTERVAL_HOURS = 2

# --- Configuration Loading ---

def load_config(config_path: str) -> configparser.ConfigParser:
    """
    加载并验证配置文件。

    Args:
        config_path (str): 配置文件的路径。

    Returns:
        configparser.ConfigParser: 加载后的 ConfigParser 对象。

    Raises:
        FileNotFoundError: 如果配置文件不存在。
        configparser.Error: 如果配置文件格式错误或缺少必要的节/选项。
    """
    if not os.path.exists(config_path):
        raise FileNotFoundError(f"配置文件未找到: {config_path}")

    config = configparser.ConfigParser()
    try:
        config.read(config_path)
        # 基本验证
        if not config.has_section('OCI'):
            raise configparser.MissingSectionHeaderError('OCI', config_path, '')
        if not config.has_section('Billing'):
            raise configparser.MissingSectionHeaderError('Billing', config_path, '')
        if not config.has_section('Alerting'):
            raise configparser.MissingSectionHeaderError('Alerting', config_path, '')

        # 验证 OCI 部分的必要选项 (target_tenancy_ocid 是可选的)
        required_oci = ['config_file', 'profile_name', 'tenancy_ocid']
        for option in required_oci:
            if not config.has_option('OCI', option):
                raise configparser.NoOptionError(option, 'OCI')

        # 验证 Billing 部分的必要选项
        required_billing = ['start_time', 'cost_threshold', 'currency']
        for option in required_billing:
            if not config.has_option('Billing', option):
                raise configparser.NoOptionError(option, 'Billing')
        # 尝试解析以验证格式
        config.getfloat('Billing', 'cost_threshold')
        datetime.strptime(config.get('Billing', 'start_time'), '%Y-%m-%dT%H:%M:%SZ')

        # 验证 Alerting 部分的必要选项
        required_alerting = ['method']
        for option in required_alerting:
            if not config.has_option('Alerting', option):
                 raise configparser.NoOptionError(option, 'Alerting')

        # 如果告警方法是 feishu，则验证 webhook url 是否存在
        alert_method = config.get('Alerting', 'method').lower()
        if alert_method == 'feishu':
            if not config.has_option('Alerting', 'feishu_webhook_url'):
                raise configparser.NoOptionError('feishu_webhook_url', 'Alerting')
            # 简单验证 URL 格式 (可选，但推荐)
            if not config.get('Alerting', 'feishu_webhook_url').startswith('https://open.feishu.cn/open-apis/bot/v2/hook/'):
                 logger.warning(f"配置文件中的 feishu_webhook_url 格式可能不正确: {config.get('Alerting', 'feishu_webhook_url')}")


    except configparser.Error as e:
        logger.error(f"加载配置文件时出错 ({config_path}): {e}")
        raise
    except ValueError as e:
         logger.error(f"配置文件中的值格式错误 ({config_path}): {e}")
         raise configparser.Error(f"配置文件中的值格式错误: {e}") from e

    logger.info(f"配置文件加载成功: {config_path}")
    return config

# --- OCI Usage Fetching ---

def get_oci_usage(config: configparser.ConfigParser, start_time_str: str) -> Optional[float]:
    """
    从 OCI 获取指定租户在指定时间段内的累计用量。

    Args:
        config (configparser.ConfigParser): 加载后的 ConfigParser 对象。
        start_time_str (str): ISO 8601 格式的起始时间字符串 (e.g., "2024-01-01T00:00:00Z")。

    Returns:
        Optional[float]: 累计花费（浮点数），如果获取失败则返回 None。
    """
    try:
        oci_config_path = os.path.expanduser(config.get('OCI', 'config_file'))
        profile_name = config.get('OCI', 'profile_name')
        # 父租户 OCID，主要用于认证
        auth_tenancy_ocid = config.get('OCI', 'tenancy_ocid')
        # 目标租户 OCID，用于查询用量。如果未配置，则回退到父租户 OCID。
        target_tenancy_ocid = config.get('OCI', 'target_tenancy_ocid', fallback=auth_tenancy_ocid)
        target_currency = config.get('Billing', 'currency')

        # 加载 OCI 配置 (使用父租户的 profile)
        oci_config = oci.config.from_file(file_location=oci_config_path, profile_name=profile_name)
        # 确保配置中的 tenancy 与认证租户匹配
        oci_config['tenancy'] = auth_tenancy_ocid
        oci.config.validate_config(oci_config) # 验证配置

        usage_api_client = oci.usage_api.UsageapiClient(oci_config)

        # 将字符串时间转换为 datetime 对象 (确保是 UTC)
        start_time_dt = datetime.strptime(start_time_str, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)
        start_time_api = start_time_dt.replace(tzinfo=None)
        end_time_api = datetime.utcnow()

        # 在日志中明确指出正在查询哪个租户
        logger.info(f"正在获取租户 {target_tenancy_ocid} 从 {start_time_dt.isoformat()} 到 {end_time_api.isoformat()} 的用量...")

        request_details = oci.usage_api.models.RequestSummarizedUsagesDetails(
            # tenant_id 应为要查询用量的租户 OCID
            tenant_id=target_tenancy_ocid,
            time_usage_started=start_time_api,
            time_usage_ended=end_time_api,
            granularity='TOTAL',
            query_type='COST'
            # compartment_depth=6 # 如果需要包含子 Compartment，可以取消注释并调整深度
        )

        summarized_usages = usage_api_client.request_summarized_usages(request_details)

        total_cost = 0.0
        if summarized_usages.data and summarized_usages.data.items:
            for item in summarized_usages.data.items:
                if hasattr(item, 'computed_amount') and item.computed_amount is not None and \
                   hasattr(item, 'currency') and hasattr(item.currency, 'iso_code') and \
                   item.currency.iso_code == target_currency:
                    total_cost += float(item.computed_amount)
                elif hasattr(item, 'computed_amount') and item.computed_amount is not None:
                     logger.warning(f"用量项的货币 ({getattr(item.currency, 'iso_code', 'N/A')}) 与目标货币 ({target_currency}) 不匹配，已跳过。金额: {item.computed_amount}")

            logger.info(f"获取到租户 {target_tenancy_ocid} 的 {len(summarized_usages.data.items)} 条用量记录。")
            return total_cost
        else:
            logger.info(f"在指定时间范围内未找到租户 {target_tenancy_ocid} 的用量数据。")
            return 0.0

    except oci.exceptions.ServiceError as e:
        logger.error(f"OCI API 请求失败 (查询租户 {target_tenancy_ocid}): {e}")
        # 特别处理权限错误
        if e.status == 401 or e.status == 404: # 404有时也表示权限不足或资源不存在
             logger.error(f"请确认父租户 ({auth_tenancy_ocid}) 的凭证具有读取子租户 ({target_tenancy_ocid}) 用量数据的权限。")
        return None
    except oci.exceptions.ConfigFileNotFound as e:
        logger.error(f"OCI 配置文件未找到: {e}")
        return None
    except oci.exceptions.ProfileNotFound as e:
        logger.error(f"OCI 配置文件中找不到指定的 profile: {e}")
        return None
    except Exception as e:
        logger.error(f"获取 OCI 用量时发生未知错误: {e}", exc_info=True)
        return None

# --- Alerting ---

def send_feishu_alert(webhook_url: str, message: str) -> None:
    """
    发送告警消息到飞书机器人。

    Args:
        webhook_url: 飞书机器人的 Webhook URL。
        message: 要发送的告警消息文本。
    """
    headers = {'Content-Type': 'application/json'}
    payload = {
        "msg_type": "text",
        "content": {
            "text": f"🚨 OCI Billing Alert 🚨\n\n{message}"
        }
    }
    try:
        response = requests.post(webhook_url, headers=headers, data=json.dumps(payload), timeout=10)
        response.raise_for_status() # 如果请求失败 (状态码 >= 400)，则抛出异常
        result = response.json()
        if result.get("StatusCode") == 0 or result.get("code") == 0: # 检查飞书返回的状态码
             logger.info("飞书告警发送成功。")
        else:
             logger.error(f"发送飞书告警时返回错误: {result}")
    except requests.exceptions.RequestException as e:
        logger.error(f"发送飞书告警时发生网络或请求错误: {e}")
    except json.JSONDecodeError:
         logger.error(f"解析飞书响应时出错: {response.text}")
    except Exception as e:
        logger.error(f"发送飞书告警时发生未知错误: {e}", exc_info=True)


def trigger_alert(method: str, message: str, config: configparser.ConfigParser) -> None:
    """
    根据配置触发告警。

    Args:
        method: 告警方法 (e.g., 'log', 'feishu').
        message: 告警消息内容。
        config: 加载后的 ConfigParser 对象。
    """
    logger.warning(f"ALERT TRIGGERED: {message}") # 总是记录日志

    method_lower = method.lower() # 转换为小写以便比较

    if method_lower == 'log':
        # 已经通过上面的 logger.warning 记录了
        pass
    elif method_lower == 'feishu':
        webhook_url = config.get('Alerting', 'feishu_webhook_url', fallback=None)
        if webhook_url:
            send_feishu_alert(webhook_url, message)
        else:
            logger.error("告警方法配置为 feishu，但未在配置文件中找到 feishu_webhook_url。")
    # --- 在这里添加其他告警方法的实现 (例如 email, slack) ---
    # elif method.lower() == 'email':
    #     send_email_alert(message, config)
    # elif method.lower() == 'slack':
    #     send_slack_alert(message, config)
    else:
        logger.error(f"不支持的告警方法配置: {method}")

# --- Main Check Logic ---

def run_check(config_path: str) -> None:
    """
    执行一次完整的账单检查和告警流程。

    Args:
        config_path (str): 配置文件的路径。
    """
    logger.info(f"开始执行账单检查 (配置文件: {config_path})...")
    try:
        config = load_config(config_path)

        start_time_str = config.get('Billing', 'start_time')
        cost_threshold = config.getfloat('Billing', 'cost_threshold')
        currency = config.get('Billing', 'currency')
        alert_method = config.get('Alerting', 'method')
        # 获取目标租户 OCID，如果未配置则为父租户 OCID
        auth_tenancy_ocid = config.get('OCI', 'tenancy_ocid')
        target_tenancy_ocid = config.get('OCI', 'target_tenancy_ocid', fallback=auth_tenancy_ocid)


        cumulative_cost = get_oci_usage(config, start_time_str)

        if cumulative_cost is None:
            logger.error(f"无法获取租户 {target_tenancy_ocid} 的累计用量，跳过本次检查。")
            return # 退出当前检查

        logger.info(f"租户 {target_tenancy_ocid} 累计用量自 {start_time_str}: {cumulative_cost:.2f} {currency}")

        if cumulative_cost > cost_threshold:
            message = (
                f"OCI 租户 {target_tenancy_ocid} 累计用量 {cumulative_cost:.2f} {currency} 已超过阈值 "
                f"{cost_threshold:.2f} {currency} (自 {start_time_str} 起)。"
            )
            trigger_alert(alert_method, message, config)
        else:
            logger.info(f"租户 {target_tenancy_ocid} 累计用量在阈值 ({cost_threshold:.2f} {currency}) 内。")

    except FileNotFoundError:
        logger.error(f"无法执行检查，配置文件未找到: {config_path}")
    except configparser.Error as e:
        logger.error(f"无法执行检查，配置文件错误: {e}")
    except Exception as e:
        logger.error(f"执行检查时发生意外错误: {e}", exc_info=True)

    logger.info("账单检查执行完毕。")


# --- Scheduling ---

def schedule_check(config_path: str, interval_hours: int) -> NoReturn:
    """
    设置定时任务以定期运行账单检查。

    Args:
        config_path: 配置文件的路径。
        interval_hours: 检查间隔的小时数。
    """
    logger.info(f"任务已安排，每 {interval_hours} 小时运行一次。按 Ctrl+C 退出。")
    # 先立即执行一次
    run_check(config_path)
    # 然后设置定时任务
    schedule.every(interval_hours).hours.do(run_check, config_path=config_path)

    while True:
        schedule.run_pending()
        time.sleep(60) # 每分钟检查一次是否有任务需要运行

# --- Entry Point ---

def main() -> None:
    """
    脚本主入口，解析参数并启动调度器。
    """
    parser = argparse.ArgumentParser(description="监控 OCI 账单并根据累计用量发出告警。")
    parser.add_argument(
        '-c', '--config',
        default=DEFAULT_CONFIG_PATH,
        help=f"配置文件的路径 (默认: {DEFAULT_CONFIG_PATH})"
    )
    parser.add_argument(
        '-i', '--interval',
        type=int,
        default=DEFAULT_INTERVAL_HOURS,
        help=f"检查间隔的小时数 (默认: {DEFAULT_INTERVAL_HOURS})"
    )
    parser.add_argument(
        '--run-once',
        action='store_true',
        help="仅执行一次检查然后退出，不启动定时任务。"
    )

    args = parser.parse_args()

    if args.interval <= 0:
        logger.error("检查间隔必须是正数。")
        sys.exit(1)

    try:
        # 在启动调度前尝试加载一次配置，以便早期发现错误
        load_config(args.config)
    except (FileNotFoundError, configparser.Error) as e:
        logger.error(f"启动失败，配置文件错误: {e}")
        sys.exit(1)
    except Exception as e: # 捕获其他可能的加载错误
         logger.error(f"启动失败，加载配置时发生未知错误: {e}")
         sys.exit(1)


    if args.run_once:
        run_check(args.config)
    else:
        try:
            schedule_check(args.config, args.interval)
        except KeyboardInterrupt:
            logger.info("收到退出信号，正在停止调度器...")
            sys.exit(0)
        except Exception as e:
            logger.error(f"调度器运行时发生错误: {e}", exc_info=True)
            sys.exit(1)


if __name__ == "__main__":
    main()