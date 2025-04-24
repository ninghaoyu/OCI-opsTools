# OCI 账单权限用户创建工具

这个工具用于创建拥有账单权限的OCI用户，并授权该用户访问"成本分析"页面和"成本和使用量报告"页面。

## 功能

- 创建新的OCI用户
- 为用户设置必要的权限策略
- 授权用户访问账单相关功能，包括：
  - 成本分析 (Cost Analysis)
  - 成本和使用量报告 (Cost and Usage Reports)

## 前提条件

- Python 3.11 或更高版本
- OCI Python SDK (`oci` 包)
- 有效的OCI配置文件 (通常位于 `~/.oci/config`)
- 具有创建用户和策略权限的OCI账户

## 安装

1. 确保已安装Python 3.11+
2. 安装OCI SDK:

```bash
pip install oci