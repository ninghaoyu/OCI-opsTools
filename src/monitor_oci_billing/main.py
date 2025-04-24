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
from typing import Optional, Dict, Any, NoReturn, Tuple # <-- 导入 Tuple
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
        # 在非 Cloud Shell 模式下，config_file 和 profile_name 是必需的
        # 但我们将在 get_oci_usage 中处理 Cloud Shell 的情况，所以这里暂时不修改验证逻辑
        # 如果在 Cloud Shell 模式下运行且缺少 config_file/profile_name，get_oci_usage 会处理
        required_oci = ['tenancy_ocid'] # 至少需要父租户 OCID
        for option in required_oci:
            if not config.has_option('OCI', option):
                raise configparser.NoOptionError(option, 'OCI')

        # 如果不是 Cloud Shell 模式（稍后判断），则需要 config_file 和 profile_name
        # 这里暂时不强制检查，让 get_oci_usage 根据模式决定

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

def get_oci_signer_and_config(config: configparser.ConfigParser, is_cloud_shell: bool) -> Tuple[oci.signer.Signer, Dict[str, Any]]:
    """
    根据运行模式（普通或 Cloud Shell）获取 OCI Signer 和基础配置。

    Args:
        config (configparser.ConfigParser): 加载后的 ConfigParser 对象。
        is_cloud_shell (bool): 是否在 Cloud Shell 模式下运行。

    Returns:
        Tuple[oci.signer.Signer, Dict[str, Any]]: 包含 Signer 对象和 OCI 配置字典的元组。

    Raises:
        oci.exceptions.ConfigFileNotFound: 如果在非 Cloud Shell 模式下配置文件未找到。
        oci.exceptions.ProfileNotFound: 如果在非 Cloud Shell 模式下 profile 未找到。
        configparser.NoOptionError: 如果缺少必要的 OCI 配置项。
        Exception: 其他 OCI SDK 或配置相关的错误。
    """
    auth_tenancy_ocid = config.get('OCI', 'tenancy_ocid') # 认证租户 OCID 总是需要

    if is_cloud_shell:
        logger.info("使用 Cloud Shell 实例主体进行认证。")
        try:
            signer = oci.auth.signers.InstancePrincipalsSecurityTokenSigner()
            # Cloud Shell 模式下，创建一个最小化的配置字典
            oci_config = {"tenancy": auth_tenancy_ocid, "region": signer.region} # 从 signer 获取区域
            return signer, oci_config
        except Exception as e:
            logger.error(f"获取 Cloud Shell 实例主体 Signer 失败: {e}", exc_info=True)
            raise
    else:
        logger.info("使用 OCI 配置文件进行认证。")
        if not config.has_option('OCI', 'config_file'):
            raise configparser.NoOptionError('config_file', 'OCI', "非 Cloud Shell 模式下需要 'config_file'")
        if not config.has_option('OCI', 'profile_name'):
            raise configparser.NoOptionError('profile_name', 'OCI', "非 Cloud Shell 模式下需要 'profile_name'")

        oci_config_path = os.path.expanduser(config.get('OCI', 'config_file'))
        profile_name = config.get('OCI', 'profile_name')

        try:
            oci_config = oci.config.from_file(file_location=oci_config_path, profile_name=profile_name)
            # 确保配置中的 tenancy 与认证租户匹配 (配置文件中的 tenancy 优先)
            if 'tenancy' not in oci_config or not oci_config['tenancy']:
                 oci_config['tenancy'] = auth_tenancy_ocid
                 logger.warning(f"OCI 配置文件 {oci_config_path} [profile {profile_name}] 中缺少 'tenancy'，已使用 config.ini 中的 'tenancy_ocid' ({auth_tenancy_ocid})。")
            elif oci_config['tenancy'] != auth_tenancy_ocid:
                 logger.warning(f"OCI 配置文件 {oci_config_path} [profile {profile_name}] 中的 'tenancy' ({oci_config['tenancy']}) 与 config.ini 中的 'tenancy_ocid' ({auth_tenancy_ocid}) 不匹配。将使用配置文件中的值进行认证。")

            oci.config.validate_config(oci_config) # 验证配置
            signer = oci.signer.Signer(
                tenancy=oci_config["tenancy"],
                user=oci_config["user"],
                fingerprint=oci_config["fingerprint"],
                private_key_file_location=oci_config.get("key_file"),
                pass_phrase=oci.config.get_config_value_or_default(oci_config, "pass_phrase"),
                private_key_content=oci_config.get("key_content")
            )
            return signer, oci_config
        except oci.exceptions.ConfigFileNotFound as e:
            logger.error(f"OCI 配置文件未找到: {e}")
            raise
        except oci.exceptions.ProfileNotFound as e:
            logger.error(f"OCI 配置文件中找不到指定的 profile: {e}")
            raise
        except Exception as e:
            logger.error(f"加载 OCI 配置或创建 Signer 时出错: {e}", exc_info=True)
            raise


def get_oci_usage(config: configparser.ConfigParser, start_time_str: str, is_cloud_shell: bool) -> Optional[float]:
    """
    从 OCI 获取指定租户在指定时间段内的累计用量。

    Args:
        config (configparser.ConfigParser): 加载后的 ConfigParser 对象。
        start_time_str (str): ISO 8601 格式的起始时间字符串 (e.g., "2024-01-01T00:00:00Z")。
        is_cloud_shell (bool): 是否在 Cloud Shell 模式下运行。

    Returns:
        Optional[float]: 累计花费（浮点数），如果获取失败则返回 None。
    """
    try:
        # 获取认证方式和基础配置
        signer, oci_base_config = get_oci_signer_and_config(config, is_cloud_shell)

        # 父租户 OCID，用于认证日志和回退
        auth_tenancy_ocid = config.get('OCI', 'tenancy_ocid')
        # 目标租户 OCID，用于查询用量。如果未配置，则回退到父租户 OCID。
        target_tenancy_ocid = config.get('OCI', 'target_tenancy_ocid', fallback=auth_tenancy_ocid)
        target_currency = config.get('Billing', 'currency')

        # 使用获取到的 signer 和 config 初始化客户端
        # 注意：UsageapiClient 构造函数不直接接受 signer，它会从传入的 config 字典或默认位置推断
        # 我们需要确保传递给 Client 的 config 包含必要的认证信息或让 Client 使用 signer
        # 最简单的方式是直接将 signer 传递给 Client 的构造函数（如果支持），或者确保 config 包含足够信息
        # 更新：UsageapiClient 可以直接接受 signer
        usage_api_client = oci.usage_api.UsageapiClient(config=oci_base_config, signer=signer)

        # 将字符串时间转换为 datetime 对象 (确保是 UTC)
        start_time_dt = datetime.strptime(start_time_str, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)
        start_time_api = start_time_dt.replace(tzinfo=None)
        end_time_api = datetime.utcnow()

        logger.info(f"正在获取租户 {target_tenancy_ocid} 从 {start_time_dt.isoformat()} 到 {end_time_api.isoformat()} 的用量...")

        request_details = oci.usage_api.models.RequestSummarizedUsagesDetails(
            tenant_id=target_tenancy_ocid,
            time_usage_started=start_time_api,
            time_usage_ended=end_time_api,
            granularity='TOTAL',
            query_type='COST'
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

    except (oci.exceptions.ConfigFileNotFound, oci.exceptions.ProfileNotFound, configparser.NoOptionError) as e:
         # 这些错误现在由 get_oci_signer_and_config 处理和记录，这里只捕获以返回 None
         logger.error(f"OCI 配置错误: {e}")
         return None
    except oci.exceptions.ServiceError as e:
        logger.error(f"OCI API 请求失败 (查询租户 {target_tenancy_ocid}): {e}")
        if e.status == 401 or e.status == 404:
             auth_method = "实例主体" if is_cloud_shell else f"配置文件用户 (profile: {config.get('OCI', 'profile_name', fallback='N/A')})"
             logger.error(f"请确认认证主体 ({auth_method}，父租户: {auth_tenancy_ocid}) 具有读取子租户 ({target_tenancy_ocid}) 用量数据的权限。")
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

def run_check(config_path: str, is_cloud_shell: bool) -> None: # <-- 添加 is_cloud_shell 参数
    """
    执行一次完整的账单检查和告警流程。

    Args:
        config_path (str): 配置文件的路径。
        is_cloud_shell (bool): 是否在 Cloud Shell 模式下运行。
    """
    logger.info(f"开始执行账单检查 (配置文件: {config_path}, Cloud Shell 模式: {is_cloud_shell})...")
    try:
        config = load_config(config_path)

        start_time_str = config.get('Billing', 'start_time')
        cost_threshold = config.getfloat('Billing', 'cost_threshold')
        currency = config.get('Billing', 'currency')
        alert_method = config.get('Alerting', 'method')
        auth_tenancy_ocid = config.get('OCI', 'tenancy_ocid')
        target_tenancy_ocid = config.get('OCI', 'target_tenancy_ocid', fallback=auth_tenancy_ocid)

        # 将 is_cloud_shell 传递给 get_oci_usage
        cumulative_cost = get_oci_usage(config, start_time_str, is_cloud_shell)

        if cumulative_cost is None:
            logger.error(f"无法获取租户 {target_tenancy_ocid} 的累计用量，跳过本次检查。")
            return

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

def schedule_check(config_path: str, interval_hours: int, is_cloud_shell: bool) -> NoReturn: # <-- 添加 is_cloud_shell 参数
    """
    设置定时任务以定期运行账单检查。

    Args:
        config_path (str): 配置文件的路径。
        interval_hours (int): 检查间隔的小时数。
        is_cloud_shell (bool): 是否在 Cloud Shell 模式下运行。
    """
    logger.info(f"任务已安排，每 {interval_hours} 小时运行一次。按 Ctrl+C 退出。")
    # 先立即执行一次
    run_check(config_path, is_cloud_shell)
    # 然后设置定时任务，传递 is_cloud_shell
    schedule.every(interval_hours).hours.do(run_check, config_path=config_path, is_cloud_shell=is_cloud_shell)

    while True:
        schedule.run_pending()
        time.sleep(60)

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
    parser.add_argument( # <-- 新增参数
        '--cloud-shell',
        action='store_true',
        help="使用 Cloud Shell 实例主体进行认证，忽略 OCI 配置文件。"
    )

    args = parser.parse_args()

    if args.interval <= 0:
        logger.error("检查间隔必须是正数。")
        sys.exit(1)

    try:
        # 尝试加载配置，即使在 Cloud Shell 模式下也需要 Billing/Alerting 等部分
        load_config(args.config)
    except (FileNotFoundError, configparser.Error) as e:
        logger.error(f"启动失败，配置文件错误: {e}")
        sys.exit(1)
    except Exception as e:
         logger.error(f"启动失败，加载配置时发生未知错误: {e}")
         sys.exit(1)


    if args.run_once:
        # 传递 cloud_shell 标志
        run_check(args.config, args.cloud_shell)
    else:
        try:
            # 传递 cloud_shell 标志
            schedule_check(args.config, args.interval, args.cloud_shell)
        except KeyboardInterrupt:
            logger.info("收到退出信号，正在停止调度器...")
            sys.exit(0)
        except Exception as e:
            logger.error(f"调度器运行时发生错误: {e}", exc_info=True)
            sys.exit(1)


if __name__ == "__main__":
    main()