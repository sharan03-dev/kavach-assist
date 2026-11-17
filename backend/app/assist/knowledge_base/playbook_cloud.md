# Cloud and SaaS mitigation playbook
## Cloud and SaaS: stolen credentials and exposed keys
Most cloud incidents start with a leaked access key, a phished admin or a public bucket. If a key is exposed: deactivate and rotate it at once, review its recent activity in the audit log (CloudTrail, Azure Activity, GCP Audit), delete resources the attacker created (crypto-miners, new users, access keys), and enable MFA for every user. Never commit keys to git; use short-lived roles.
## Cloud and SaaS: containers and Kubernetes
Rebuild images on patched base images, do not run containers as root or privileged, do not mount the Docker socket into application containers unless required, restrict the Kubernetes API to trusted networks, use network policies and RBAC with least privilege, and scan images for known CVEs before deploying.
## Cloud and SaaS: hardening baseline
MFA everywhere, least-privilege IAM, no public storage unless intended, encryption at rest and in transit, central logging with alerts on root or admin use, backups in another account, and budgets / alerts to catch abuse.
