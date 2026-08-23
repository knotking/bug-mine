terraform {
  required_version = ">= 1.9"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
    google-beta = {
      source  = "hashicorp/google-beta"
      version = "~> 6.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }

  backend "gcs" {
    bucket = "bugmine-dev-tfstate"
    prefix = "env/dev"
  }
}

# user_project_override makes the provider send the quota project header. Without it,
# identitytoolkit refuses Application Default Credentials outright — setting the quota project
# on ADC is necessary but not sufficient, because Terraform has to actually forward it.
provider "google" {
  project               = var.project_id
  region                = var.region
  user_project_override = true
  billing_project       = var.project_id
}

provider "google-beta" {
  project               = var.project_id
  region                = var.region
  user_project_override = true
  billing_project       = var.project_id
}
