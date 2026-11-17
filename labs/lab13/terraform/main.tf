# Lab 13 - Terraform for a MongoDB Atlas FREE-TIER (M0) cluster - corrected version of chapter-13/main.tf
# (the reference file is kept unchanged in reference/main.tf; the differences are explained in ../README.md).
#
#   export MONGODB_ATLAS_PUBLIC_KEY=...  MONGODB_ATLAS_PRIVATE_KEY=...      # Atlas organisation API key
#   cp terraform.tfvars.example terraform.tfvars   (org id, password, YOUR public IP)
#   terraform init && terraform plan && terraform apply
#   terraform output -raw cluster_uri   -> put it in ../../../.env as ATLAS_URI=... and run `lab13 cloud`
#
# Checked by `lab13 cloud` with: terraform init -backend=false && terraform validate

terraform {
  required_version = ">= 1.5.0"
  required_providers {
    mongodbatlas = {
      source  = "mongodb/mongodbatlas"
      version = "~> 1.16"          # 1.x: replication_specs / region_configs are blocks
    }
  }
}

provider "mongodbatlas" {}          # keys from MONGODB_ATLAS_PUBLIC_KEY / MONGODB_ATLAS_PRIVATE_KEY

variable "atlas_org_id" {
  description = "MongoDB Atlas organisation id"
  type        = string
}

variable "atlas_user_password" {
  description = "Password of the application database user"
  type        = string
  sensitive   = true
  validation {
    condition     = length(var.atlas_user_password) >= 16
    error_message = "Use at least 16 characters (Lab 12: weak passwords are refused)."
  }
}

variable "access_cidr" {
  description = "The ONLY network allowed to connect, e.g. your public IP as a.b.c.d/32"
  type        = string
  validation {
    condition     = can(cidrhost(var.access_cidr, 0)) && var.access_cidr != "0.0.0.0/0"
    error_message = "Give a real CIDR such as 203.0.113.7/32 - 0.0.0.0/0 opens the cluster to the whole internet."
  }
}

variable "app_database" {
  description = "Database the application user may read and write"
  type        = string
  default     = "cti_billing"
}

resource "mongodbatlas_project" "nosql_labs" {
  name   = "NoSQL Labs - Training"
  org_id = var.atlas_org_id
}

# M0 is a shared ("TENANT") cluster hosted on AWS: provider_name must be TENANT and the cloud goes in
# backing_provider_name. Backup, disk auto-scaling and the MongoDB version are fixed by Atlas on shared tiers.
resource "mongodbatlas_advanced_cluster" "training" {
  project_id   = mongodbatlas_project.nosql_labs.id
  name         = "training-cluster"
  cluster_type = "REPLICASET"

  replication_specs {
    region_configs {
      electable_specs {
        instance_size = "M0"
      }
      provider_name         = "TENANT"
      backing_provider_name = "AWS"
      region_name           = "AP_SOUTH_1"   # Mumbai - closest Atlas region to Bengaluru
      priority              = 7
    }
  }
}

resource "mongodbatlas_database_user" "lab_user" {
  project_id         = mongodbatlas_project.nosql_labs.id
  username           = "lab_user"
  password           = var.atlas_user_password
  auth_database_name = "admin"

  roles {                                    # least privilege: one database, not readWriteAnyDatabase
    role_name     = "readWrite"
    database_name = var.app_database
  }

  scopes {                                   # the user is valid only on this cluster
    name = mongodbatlas_advanced_cluster.training.name
    type = "CLUSTER"
  }
}

resource "mongodbatlas_project_ip_access_list" "me" {
  project_id = mongodbatlas_project.nosql_labs.id
  cidr_block = var.access_cidr
  comment    = "Lab 13 - only this address"
}

locals {
  srv_host = replace(mongodbatlas_advanced_cluster.training.connection_strings[0].standard_srv, "mongodb+srv://", "")
}

output "connection_string" {
  description = "SRV connection string (no credentials)"
  value       = mongodbatlas_advanced_cluster.training.connection_strings[0].standard_srv
}

output "cluster_uri" {
  description = "Full URI for PyMongo (ATLAS_URI)"
  value       = "mongodb+srv://lab_user:${urlencode(var.atlas_user_password)}@${local.srv_host}/?retryWrites=true&w=majority&appName=lab13"
  sensitive   = true
}
