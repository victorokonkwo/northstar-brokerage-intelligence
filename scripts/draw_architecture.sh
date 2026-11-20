#!/usr/bin/env bash
# Redraw the AWS architecture diagrams in docs/architecture/aws/ from the Terraform stacks
# with TerraVision (`make diagrams`).
#
# Needs `aws login`, Graphviz, Terraform, and uv. Terraform validates the credentials and
# reads the caller identity and the availability zones; nothing else is read from AWS.
# Copies of the stacks are planned against empty local state in a temporary directory, so
# the stacks' S3 state is never read and nothing is applied. The required variables get
# placeholder values (example.com, RFC 5737), which change no resource's shape.
set -euo pipefail

TERRAVISION=(uvx --from terravision==0.56.4 terravision)
root="$(git rev-parse --show-toplevel)"
out="$root/docs/architecture/aws"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

eval "$(aws configure export-credentials --format env)"
export AWS_REGION=ca-central-1

printf 'budget_alert_email = "alerts@example.com"\n' >"$work/bootstrap.tfvars"
printf 'operator_cidrs = ["203.0.113.7/32"]\n' >"$work/data.tfvars"

for stack in bootstrap data; do
  mkdir -p "$work/infrastructure/terraform/$stack"
  cp "$root/infrastructure/terraform/$stack/"*.tf \
    "$root/infrastructure/terraform/$stack/.terraform.lock.hcl" \
    "$work/infrastructure/terraform/$stack/"
  (
    cd "$work"
    "${TERRAVISION[@]}" graphdata --source "infrastructure/terraform/$stack" \
      --varfile "$stack.tfvars" --outfile "$stack.raw.tvg.json" >/dev/null
    uv run --project "$root" python "$root/scripts/terravision_prune.py" \
      "$stack" "$stack.raw.tvg.json" "$stack.tvg.json"
    "${TERRAVISION[@]}" draw --source "$stack.tvg.json" \
      --annotate "$out/$stack.terravision.yml" --format svg --outfile "$stack-stack"
  )
  mv "$work/$stack-stack".*svg "$out/$stack-stack.svg"
done
