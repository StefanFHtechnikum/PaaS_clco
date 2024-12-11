import pulumi
from pulumi import Config
from pulumi_azure_native import resources, cognitiveservices, network, web, costmanagement
import pulumi_azure_native as azure_native
import json
import uuid

# location: the Azure region where all resources will be created
# email: the email address for budget notifications
# subscription_id: the specific subscription where the resources will be deployed
config = Config()
location = config.get("location") or "westeurope"
email = "wi24b100@technikum-wien.at"
subscription_id = "64b584d9-7fa6-49e0-ae15-d41006efcf1b"

# 1. Create a Resource Group
# This Resource Group holds all the resources for our PaaS project.
resource_group = resources.ResourceGroup(
    "paas_resource_group",
    resource_group_name="paas-rg",  # fixed Resource Group name
    location=location
)
pulumi.export("resource_group_name", resource_group.name)

# 2. Create a Virtual Network and Subnets
# VNet with two subnets created:
# - app-subnet: used for App Service VNet integration.
# - endpoint-subnet: used for the Private Endpoint to Cognitive Services.
vnet = network.VirtualNetwork(
    "paas_vnet",
    resource_group_name="paas-rg",
    virtual_network_name="paas-vnet",
    location=location,
    address_space=network.AddressSpaceArgs(
        address_prefixes=["10.10.0.0/16"]
    )
)

app_subnet = network.Subnet(
    "app_subnet",
    resource_group_name="paas-rg",
    virtual_network_name=vnet.name,
    subnet_name="app-subnet",
    address_prefix="10.10.1.0/24",
    delegations=[network.DelegationArgs(
        name="appDelegation",
        service_name="Microsoft.Web/serverFarms"
    )]
)

endpoint_subnet = network.Subnet(
    "endpoint_subnet",
    resource_group_name="paas-rg",
    virtual_network_name=vnet.name,
    subnet_name="endpoint-subnet",
    address_prefix="10.10.2.0/24",
    # Disabling network policies for the endpoint subnet to allow Private Endpoint.
    private_endpoint_network_policies="Disabled"
)

pulumi.export("vnet_name", vnet.name)
pulumi.export("app_subnet_name", app_subnet.name)
pulumi.export("endpoint_subnet_name", endpoint_subnet.name)

# 3. Private DNS Zone
# Create a private DNS zone for 'privatelink.cognitiveservices.azure.com' so that
# the Web App can resolve the Cognitive Services endpoint privately via the Private Endpoint.
private_dns_zone = network.PrivateZone(
    "private_dns_zone",
    resource_group_name="paas-rg",
    private_zone_name="privatelink.cognitiveservices.azure.com",
    location="global"
)

# Link the VNet to this Private DNS Zone so that the VNet can use it for name resolution.
vnet_dns_link = network.VirtualNetworkLink(
    "vnet_dns_link",
    resource_group_name="paas-rg",
    private_zone_name=private_dns_zone.name,
    virtual_network=azure_native.network.SubResourceArgs(id=vnet.id),
    registration_enabled=False,
    location="global"
)

pulumi.export("private_dns_zone_name", private_dns_zone.name)

# 4. Existing Cognitive Services Account (F0) named "ass7"
ass7_account_name = "ass7"
cognitive_endpoint = f"https://{ass7_account_name}.cognitiveservices.azure.com/"

# Retrieve the keys for the existing "ass7" Cognitive Services account.
cog_keys = cognitiveservices.list_account_keys_output(
    resource_group_name="paas-rg",
    account_name=ass7_account_name
)

cog_account_id = f"/subscriptions/{subscription_id}/resourceGroups/paas-rg/providers/Microsoft.CognitiveServices/accounts/{ass7_account_name}"

pulumi.export("cognitive_service_name", ass7_account_name)
pulumi.export("cognitive_endpoint", cognitive_endpoint)
pulumi.export("cognitive_key", cog_keys.key1)

# 5. Private Endpoint for "ass7"
# Create a private endpoint to allow the Web App to communicate with Cognitive Services privately.
cog_private_endpoint = network.PrivateEndpoint(
    "cogPrivateEndpoint",
    resource_group_name="paas-rg",
    private_endpoint_name="cog-pe",
    location=location,
    subnet=azure_native.network.SubResourceArgs(id=endpoint_subnet.id),
    private_link_service_connections=[
        network.PrivateLinkServiceConnectionArgs(
            name="cogConnection",
            private_link_service_id=cog_account_id,
            group_ids=["account"]
        )
    ]
)

# Associate the Private Endpoint with the DNS zone so that the Cognitive Services hostname
# resolves to the private IP from the endpoint.
cog_private_dns_zone_group = network.PrivateDnsZoneGroup(
    "cogPrivateDnsZoneGroup",
    private_dns_zone_group_name="cogZoneGroup",
    resource_group_name="paas-rg",
    private_endpoint_name=cog_private_endpoint.name,
    private_dns_zone_configs=[
        network.PrivateDnsZoneConfigArgs(
            name="cogDnsConfig",
            private_dns_zone_id=private_dns_zone.id
        )
    ]
)

# 6. App Service Plan and Web App (Python 3.9)
# The App Service Plan (Premium tier, 3 workers) provides scalable compute.
app_service_plan = web.AppServicePlan(
    "appServicePlan",
    resource_group_name="paas-rg",
    name="paas-asp",
    location=location,
    kind="linux",
    sku=web.SkuDescriptionArgs(
        name="P1v2",
        tier="Premium",
        capacity=3  # This ensures we have three workers
    ),
    reserved=True
)

# A fixed name for the Web App to ensure consistent URL
web_app_name = "paas-webapp-demo-group-6"

# Create the Web App using the App Service Plan and Python 3.9 runtime.
web_app = web.WebApp(
    "webApp",
    resource_group_name="paas-rg",
    name=web_app_name,
    location=location,
    server_farm_id=app_service_plan.id,
    https_only=True,
    kind="app,linux",
    site_config=web.SiteConfigArgs(
        linux_fx_version="PYTHON|3.9",
        always_on=True,
        ftps_state="Disabled"
    )
)

# Integrate the Web App with the VNet.
web_app_vnet_connection = web.WebAppSwiftVirtualNetworkConnection(
    "webAppVnetConnection",
    name=web_app.name,
    resource_group_name="paas-rg",
    subnet_resource_id=app_subnet.id
)

# Set application settings, including the Cognitive Services endpoint and key,
# so the Web App can communicate with the Text Analytics API.
app_settings = web.WebAppApplicationSettings(
    "webAppSettings",
    name=web_app.name,
    resource_group_name="paas-rg",
    properties={
        "COG_SERVICES_ENDPOINT": cognitive_endpoint,
        "COG_SERVICES_KEY": cog_keys.key1,
        "WEBSITE_RUN_FROM_PACKAGE": "0"
    }
)

pulumi.export("web_app_url", web_app.default_host_name.apply(lambda host: f"https://{host}"))

# Link the Web App to a GitHub repository to automatically fetch and deploy code.
# We set is_manual_integration=True meaning we may need to trigger deployment manually.
# If we wanted automatic deployment on commit, we could set is_manual_integration=False.
web_app_source_control = web.WebAppSourceControl(
    "webAppSourceControl",
    name=web_app.name,
    resource_group_name="paas-rg",
    repo_url="https://github.com/StefanFHtechnikum/clco-demo",
    branch="main",
    is_git_hub_action=False,
    is_manual_integration=True,
    deployment_rollback_enabled=False
)

# 7. Create a Budget resource at subscription level
# This helps control costs. The start date should be current or future month.
budget_name = f"myBudget{uuid.uuid4().hex[:8]}"
my_budget = costmanagement.Budget(
    budget_name,
    scope=f"/subscriptions/{subscription_id}",
    amount=10,
    category="Cost",
    time_grain="Monthly",
    time_period=costmanagement.BudgetTimePeriodArgs(
        start_date="2024-12-01T00:00:00Z",  # a future date
        end_date="2025-12-31T00:00:00Z"
    ),
    notifications={
        "Actual_GreaterThan_80_Percent": costmanagement.NotificationArgs(
            enabled=True,
            operator="GreaterThan",
            threshold=80,
            contact_emails=[email],
            threshold_type="Actual"
        ),
        "Forecasted_GreaterThan_100_Percent": costmanagement.NotificationArgs(
            enabled=True,
            operator="GreaterThan",
            threshold=100,
            contact_emails=[email],
            threshold_type="Forecasted"
        )
    }
)
