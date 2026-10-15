#!/usr/bin/env bash
# Creates the AWS side of starting the runner's server on demand: a role that may start that one instance, a
# Lambda function that does it when GitHub reports a queued job, and the function's URL. Run once by the account's
# owner with an administrator profile; running it again updates what exists instead of duplicating it.
# Usage: create-starter.sh --instance-id i-... --region eu-west-1 --secret-file FILE --profile NAME
#        [--function-name pf-runner-starter] [--labels self-hosted,pf]
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
instance_id="" region="" secret_file="" profile=""
function_name="pf-runner-starter"
labels="self-hosted,pf"

usage() {
  sed -n '2,6p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//' >&2
  exit 2
}

while [ $# -gt 0 ]; do
  [ $# -ge 2 ] || usage
  case "$1" in
    --instance-id) instance_id="$2" ;;
    --region) region="$2" ;;
    --secret-file) secret_file="$2" ;;
    --profile) profile="$2" ;;
    --function-name) function_name="$2" ;;
    --labels) labels="$2" ;;
    *) usage ;;
  esac
  shift 2
done
{ [ -n "$instance_id" ] && [ -n "$region" ] && [ -n "$secret_file" ] && [ -n "$profile" ]; } || usage
if [ ! -s "$secret_file" ]; then
  echo "create-starter: $secret_file is missing or empty" >&2
  exit 1
fi

aws_() { aws --profile "$profile" --region "$region" "$@"; }
role_name="${function_name}-role"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

account_id="$(aws_ sts get-caller-identity --query Account --output text)"
echo "Using AWS account $account_id in $region (profile $profile). Free: this only reads who you are."

# 1. The role: trusted by Lambda, allowed to start this one instance and write its own logs.
sed -e "s/REGION/$region/g" -e "s/ACCOUNT_ID/$account_id/g" -e "s/INSTANCE_ID/$instance_id/g" \
  -e "s/FUNCTION_NAME/$function_name/g" "$here/iam-policy.json" >"$work/policy.json"
role_created=0
if aws_ iam get-role --role-name "$role_name" >/dev/null 2>&1; then
  echo "Role $role_name exists; updating its permissions. Free: IAM roles cost nothing."
else
  echo "Creating role $role_name, trusted by lambda.amazonaws.com. Free: IAM roles cost nothing."
  aws_ iam create-role --role-name "$role_name" --assume-role-policy-document "file://$here/trust-policy.json" \
    --description "Starts the ProductForge runner's instance when GitHub queues a job" >/dev/null
  role_created=1
fi
echo "Setting the role's policy: ec2:StartInstances on $instance_id only, ec2:DescribeInstances, its own log group."
aws_ iam put-role-policy --role-name "$role_name" --policy-name "$function_name" \
  --policy-document "file://$work/policy.json"
role_arn="arn:aws:iam::$account_id:role/$role_name"

# 2. The log group, with a short retention. Cost: nothing at this volume (5 GB of logs a month are free).
echo "Creating the log group /aws/lambda/$function_name, kept 14 days. Free at this volume."
aws_ logs create-log-group --log-group-name "/aws/lambda/$function_name" 2>/dev/null || true
aws_ logs put-retention-policy --log-group-name "/aws/lambda/$function_name" --retention-in-days 14

# 3. The function, from a zip of handler.py. The secret goes in by file, never on a command line or to the screen.
(cd "$here" && python3 -m zipfile -c "$work/starter.zip" handler.py)
python3 - "$secret_file" "$instance_id" "$labels" >"$work/environment.json" <<'PY'
import json
import sys

secret_file, instance_id, labels = sys.argv[1:]
secret = open(secret_file).read().strip()
print(json.dumps({"Variables": {"WEBHOOK_SECRET": secret, "INSTANCE_ID": instance_id, "RUNNER_LABELS": labels}}))
PY
chmod 600 "$work/environment.json"
if aws_ lambda get-function --function-name "$function_name" >/dev/null 2>&1; then
  echo "Function $function_name exists; updating its code and settings. Free: 1 million calls a month are free."
  aws_ lambda update-function-code --function-name "$function_name" --zip-file "fileb://$work/starter.zip" >/dev/null
  aws_ lambda wait function-updated --function-name "$function_name"
  aws_ lambda update-function-configuration --function-name "$function_name" --role "$role_arn" \
    --timeout 180 --environment "file://$work/environment.json" >/dev/null
else
  echo "Creating function $function_name (Python 3.12, arm64, 128 MB, 180 s). Free: 1 million calls a month."
  [ "$role_created" -eq 0 ] || sleep "${PF_STARTER_ROLE_WAIT:-10}" # a new role takes a few seconds to be usable by Lambda
  attempt=1
  until aws_ lambda create-function --function-name "$function_name" --runtime python3.12 --architectures arm64 \
    --handler handler.lambda_handler --role "$role_arn" --timeout 180 --memory-size 128 \
    --zip-file "fileb://$work/starter.zip" --environment "file://$work/environment.json" >/dev/null 2>"$work/err"; do
    if [ "$attempt" -ge 6 ]; then
      cat "$work/err" >&2
      exit 1
    fi
    echo "  the role is not usable yet; trying again in 10 s"
    attempt=$((attempt + 1))
    sleep "${PF_STARTER_ROLE_WAIT:-10}"
  done
fi
aws_ lambda wait function-active-v2 --function-name "$function_name"
# The URL is public, so a flood of requests must not fan out: at most 2 run at once, and the rest are throttled.
echo "Limiting the function to 2 concurrent runs. Free."
aws_ lambda put-function-concurrency --function-name "$function_name" --reserved-concurrent-executions 2 >/dev/null

# 4. The function URL. Its auth type is NONE because GitHub cannot sign AWS requests; the function checks
# GitHub's own signature instead, and refuses every request without a valid one.
if aws_ lambda get-function-url-config --function-name "$function_name" >/dev/null 2>&1; then
  echo "The function URL exists. Free: function URLs cost nothing."
else
  echo "Creating the function URL, auth type NONE (the function verifies GitHub's signature). Free."
  aws_ lambda create-function-url-config --function-name "$function_name" --auth-type NONE >/dev/null
fi
allow() { # a public-invoke statement; one that already exists is fine
  local out
  if ! out="$(aws_ lambda add-permission --function-name "$function_name" --principal '*' "$@" 2>&1)"; then
    case "$out" in *ResourceConflictException*) ;; *)
      echo "$out" >&2
      exit 1
      ;;
    esac
  fi
}
allow --statement-id pf-url-public --action lambda:InvokeFunctionUrl --function-url-auth-type NONE
allow --statement-id pf-url-public-invoke --action lambda:InvokeFunction --invoked-via-function-url

url="$(aws_ lambda get-function-url-config --function-name "$function_name" --query FunctionUrl --output text)"
echo
echo "Done. Total cost at this volume: about \$0 a month (Lambda's free tier)."
echo "Set the organisation webhook's Payload URL to:"
echo "$url"
