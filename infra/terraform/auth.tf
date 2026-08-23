# Identity Platform (Firebase Authentication).
#
# Firebase holds passwords so BugMine does not. The safest way to handle that credential class
# is not to have it — no hashing to get wrong, no reset flow to build, no breach surface here.

resource "google_identity_platform_config" "auth" {
  project = var.project_id

  sign_in {
    allow_duplicate_emails = false

    email {
      enabled = true
      # Verification is not required to sign in yet — see A-A1 in docs/architecture/auth.md.
      # Accounts only exist by invitation, so an unverified address has already been proven
      # reachable by whoever redeemed the invite sent to it.
      password_required = true
    }
  }

  depends_on = [google_project_service.enabled]
}

output "firebase_project" {
  value = var.project_id
}
