variable "resource_group_name" {
  description = "Existing resource group to deploy into."
  type        = string
}

variable "location" {
  description = "Azure region."
  type        = string
}

variable "github_owner_id" {
  description = "Numeric GitHub organization or enterprise ID."
  type        = string
}

variable "github_owner_type" {
  description = "organization or enterprise."
  type        = string
  default     = "organization"
}

variable "cluster_name" {
  description = "Kusto cluster name. Must be globally unique and lowercase."
  type        = string

  validation {
    condition     = can(regex("^[a-z][a-z0-9]{3,21}$", var.cluster_name))
    error_message = "cluster_name must be 4-22 characters, start with a letter, and be lowercase alphanumeric."
  }
}

variable "sku_name" {
  description = "Cluster SKU. The Dev/No SLA SKU is the cheapest and is fine for testing."
  type        = string
  default     = "Dev(No SLA)_Standard_E2a_v4"
}

variable "sku_capacity" {
  description = "Node count. Dev SKUs only support 1."
  type        = number
  default     = 1
}

variable "database_name" {
  description = "Kusto database name."
  type        = string
  default     = "ActionsDataStream"
}

variable "table_name" {
  description = "Table that events are ingested into."
  type        = string
  default     = "ActionsEvents"
}

variable "mapping_name" {
  description = "JSON ingestion mapping name."
  type        = string
  default     = "ActionsEventsMapping"
}

variable "ingestion_type" {
  description = "streaming or queued. Streaming has lower latency; queued is more forgiving at high volume."
  type        = string
  default     = "streaming"

  validation {
    condition     = contains(["streaming", "queued"], var.ingestion_type)
    error_message = "ingestion_type must be streaming or queued."
  }
}

variable "hot_cache_period" {
  description = "How long data stays in hot cache."
  type        = string
  default     = "P31D"
}

variable "soft_delete_period" {
  description = "How long data is retained."
  type        = string
  default     = "P365D"
}

variable "identity_name" {
  description = "Name of the user-assigned managed identity."
  type        = string
  default     = "actions-data-stream"
}

variable "create_deployer_admin" {
  description = "Grant the Terraform principal Admin on the database. Azure already does this automatically for whoever creates the database, so this is only needed when adopting a pre-existing cluster you do not already administer."
  type        = bool
  default     = false
}

variable "viewer_group_object_id" {
  description = "Object ID of an Entra security group to grant Viewer on the database. Without this, only the deployer can read the data — there is no tenant-wide default. Must be security-enabled; Kusto rejects Microsoft 365 / Unified groups. Note that Fabric dashboard permissions are separate from these ACLs, so sharing a dashboard alone is not enough."
  type        = string
  default     = null
}

variable "create_analytics" {
  description = "Create the silver/gold analytics layer: Jobs and Runs materialized views, JobFacts() and RunFacts() functions, and an OtelSpans() function that emits OTel cicd.* spans. See modules/azure-kusto/kql/analytics.kql."
  type        = bool
  default     = true
}

variable "create_enrichment" {
  description = "Create the enrichment layer: WorkflowSteps and WorkflowLogs tables plus StepFacts(), StepStats(), JobTimeSplit(), StepFailures(), OtelStepSpans() and friends. Schema only -- populate it with scripts/ingest-steps.py and scripts/ingest-logs.py, which fetch step timings and log bodies from the REST API using ids the stream emits. See modules/azure-kusto/kql/enrichment.kql."
  type        = bool
  default     = true
}

variable "tags" {
  description = "Tags applied to created resources."
  type        = map(string)
  default     = {}
}
