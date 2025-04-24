#!/bin/bash

# 脚本名称: oci_create_users.sh
# 描述: 创建OCI billing用户组，添加用户并授予成本查看权限
# 作者: Trae AI
# 日期: $(date +%Y-%m-%d)

# 设置错误处理
set -e

# 显示帮助信息
show_help() {
    echo "用法: $0 [选项]"
    echo "选项:"
    echo "  -e, --email EMAIL       用户邮箱地址 (必需)"
    echo "  -n, --name NAME         用户名称 (必需)"
    echo "  -d, --description DESC  用户描述 (可选)"
    echo "  -c, --compartment-id ID 区间ID (可选，默认自动获取根区间ID)"
    echo "  -i, --identity-domain DOMAIN  身份域名称 (可选，默认为'Default')"
    echo "  -h, --help              显示帮助信息"
    echo
    echo "示例: $0 -e user@example.com -n \"John Doe\" -d \"财务部门用户\" -i Default"
    exit 1
}

# 检查OCI CLI是否已安装
check_oci_cli() {
    if ! command -v oci &> /dev/null; then
        echo "错误: OCI CLI 未安装。请先安装 OCI CLI。"
        echo "安装指南: https://docs.oracle.com/en-us/iaas/Content/API/SDKDocs/cliinstall.htm"
        exit 1
    fi
}

# 检查OCI CLI配置
check_oci_config() {
    if ! oci iam region list &> /dev/null; then
        echo "错误: OCI CLI 未正确配置或无法连接到 OCI。"
        echo "请运行 'oci setup config' 配置您的 OCI CLI。"
        exit 1
    fi
}

# 获取租户(根区间)ID
get_tenancy_id() {
    echo "正在获取租户ID..."
    tenancy_id=$(oci iam compartment list --all --compartment-id-in-subtree true --access-level ACCESSIBLE --include-root --query "data[?\"compartment-id\" == null].id | [0]" --raw-output)
    
    if [[ -z "$tenancy_id" ]]; then
        echo "错误: 无法获取租户ID。请检查您的OCI CLI配置和权限。"
        exit 1
    fi
    
    echo "成功获取租户ID: $tenancy_id"
    echo "$tenancy_id"
}

# 创建billing用户组
create_billing_group() {
    local compartment_id=$1
    local identity_domain=$2
    local group_name="billing"
    local group_description="具有成本查看权限的用户组 (${identity_domain}域)"
    
    echo "检查${identity_domain}域下的billing用户组是否已存在..."
    if oci iam group list --compartment-id "$compartment_id" --name "$group_name" | grep -q "\"name\": \"$group_name\""; then
        echo "billing用户组已存在，跳过创建步骤。"
        # 获取组ID
        group_id=$(oci iam group list --compartment-id "$compartment_id" --name "$group_name" --query "data[0].id" --raw-output)
    else
        echo "创建billing用户组..."
        group_response=$(oci iam group create --compartment-id "$compartment_id" --name "$group_name" --description "$group_description")
        group_id=$(echo "$group_response" | grep -o '"id": "[^"]*' | cut -d'"' -f4)
        echo "billing用户组创建成功，ID: $group_id"
    fi
    
    echo "$group_id"
}

# 创建用户
create_user() {
    local compartment_id=$1
    local email=$2
    local name=$3
    local description=$4
    local identity_domain=$5
    
    echo "检查${identity_domain}域下的用户 $email 是否已存在..."
    if oci iam user list --compartment-id "$compartment_id" --name "$name" | grep -q "\"name\": \"$name\""; then
        echo "用户 $name 已存在，跳过创建步骤。"
        # 获取用户ID
        user_id=$(oci iam user list --compartment-id "$compartment_id" --name "$name" --query "data[0].id" --raw-output)
    else
        echo "创建用户 $name..."
        user_response=$(oci iam user create --compartment-id "$compartment_id" --name "$name" --email "$email" --description "$description")
        user_id=$(echo "$user_response" | grep -o '"id": "[^"]*' | cut -d'"' -f4)
        echo "用户创建成功，ID: $user_id"
    fi
    
    echo "$user_id"
}

# 将用户添加到用户组
add_user_to_group() {
    local user_id=$1
    local group_id=$2
    local identity_domain=$3
    
    echo "检查用户是否已在${identity_domain}域的billing组中..."
    if oci iam group list-users --group-id "$group_id" | grep -q "\"id\": \"$user_id\""; then
        echo "用户已在billing组中，跳过添加步骤。"
    else
        echo "将用户添加到billing组..."
        oci iam group add-user --user-id "$user_id" --group-id "$group_id"
        echo "用户已成功添加到billing组。"
    fi
}

# 创建成本查看策略
create_cost_policies() {
    local compartment_id=$1
    local identity_domain=$2
    local group_name="billing"
    local policy_name="BillingCostViewPolicy"
    local policy_description="允许${identity_domain}域中的billing组查看成本分析和使用量报告"
    local policy_statements="Allow group '${identity_domain}'/'$group_name' to read usage-reports in tenancy, \
        Allow group '${identity_domain}'/'$group_name' to manage usage-report in tenancy, \
        define tenancy usage-report as ocid1.tenancy.oc1..aaaaaaaaned4fkpkisbwjlr56u7cj63lf3wffbilvqknstgtvzub7vhqkggq, \
        endorse group '${identity_domain}'/'billing' to read objects in tenancy usage-report"
    
    echo "检查成本查看策略是否已存在..."
    if oci iam policy list --compartment-id "$compartment_id" --name "$policy_name" | grep -q "\"name\": \"$policy_name\""; then
        echo "成本查看策略已存在，跳过创建步骤。"
    else
        echo "创建成本查看策略..."
        oci iam policy create --compartment-id "$compartment_id" --name "$policy_name" --description "$policy_description" --statements "[$policy_statements]"
        echo "成本查看策略创建成功。"
    fi
}

# 主函数
main() {
    # 检查OCI CLI
    check_oci_cli
    check_oci_config
    
    # 设置默认域名
    IDENTITY_DOMAIN="Default"
    
    # 解析命令行参数
    while [[ $# -gt 0 ]]; do
        case "$1" in
            -e|--email)
                EMAIL="$2"
                shift 2
                ;;
            -n|--name)
                NAME="$2"
                shift 2
                ;;
            -d|--description)
                DESCRIPTION="$2"
                shift 2
                ;;
            -c|--compartment-id)
                COMPARTMENT_ID="$2"
                shift 2
                ;;
            -i|--identity-domain)
                IDENTITY_DOMAIN="$2"
                shift 2
                ;;
            -h|--help)
                show_help
                ;;
            *)
                echo "未知选项: $1"
                show_help
                ;;
        esac
    done
    
    # 检查必需参数
    if [[ -z "$EMAIL" || -z "$NAME" ]]; then
        echo "错误: 缺少必需参数。"
        show_help
    fi
    
    # 设置默认描述（如果未提供）
    if [[ -z "$DESCRIPTION" ]]; then
        DESCRIPTION="Billing user for cost analysis"
    fi
    
    # 如果未提供区间ID，则自动获取租户ID
    if [[ -z "$COMPARTMENT_ID" ]]; then
        COMPARTMENT_ID=$(get_tenancy_id)
    fi
    
    echo "=== 开始创建OCI用户和权限 ==="
    
    # 创建billing用户组
    GROUP_ID=$(create_billing_group "$COMPARTMENT_ID" "$IDENTITY_DOMAIN")
    
    # 创建用户
    USER_ID=$(create_user "$COMPARTMENT_ID" "$EMAIL" "$NAME" "$DESCRIPTION" "$IDENTITY_DOMAIN")
    
    # 将用户添加到billing组
    add_user_to_group "$USER_ID" "$GROUP_ID" "$IDENTITY_DOMAIN"
    
    # 创建成本查看策略
    create_cost_policies "$COMPARTMENT_ID" "$IDENTITY_DOMAIN"
    
    echo "=== OCI用户创建和权限设置完成 ==="
    echo "用户 $NAME ($EMAIL) 已创建并添加到$IDENTITY_DOMAIN域的billing组"
    echo "用户现在可以查看成本分析和使用量报告"
}

# 执行主函数
main "$@"