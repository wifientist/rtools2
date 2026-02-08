#!/bin/bash
#
# Fileshare S3 Bucket Setup Script
#
# This script creates and configures an S3 bucket for the rtools2 fileshare feature.
# Run from the scripts/aws directory.
#
# Prerequisites:
#   - AWS CLI installed and configured (aws configure)
#   - Appropriate AWS permissions to create S3 buckets and IAM resources
#

set -e

# Configuration - EDIT THESE
BUCKET_NAME="${1:-rtools-fileshare-prod}"
REGION="${2:-us-east-1}"
IAM_USER_NAME="rtools-fileshare-user"
IAM_POLICY_NAME="rtools-fileshare-policy"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "=============================================="
echo "Fileshare S3 Bucket Setup"
echo "=============================================="
echo "Bucket Name: $BUCKET_NAME"
echo "Region: $REGION"
echo ""

# Check if bucket already exists
if aws s3api head-bucket --bucket "$BUCKET_NAME" 2>/dev/null; then
    echo "Bucket '$BUCKET_NAME' already exists."
    read -p "Continue with configuration? (y/n) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
else
    echo "Creating S3 bucket..."
    if [ "$REGION" == "us-east-1" ]; then
        aws s3api create-bucket \
            --bucket "$BUCKET_NAME" \
            --region "$REGION"
    else
        aws s3api create-bucket \
            --bucket "$BUCKET_NAME" \
            --region "$REGION" \
            --create-bucket-configuration LocationConstraint="$REGION"
    fi
    echo "✓ Bucket created"
fi

echo ""
echo "Blocking public access..."
aws s3api put-public-access-block \
    --bucket "$BUCKET_NAME" \
    --public-access-block-configuration \
    "BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true"
echo "✓ Public access blocked"

echo ""
echo "Applying lifecycle rules (30-day expiry, abort incomplete uploads)..."
aws s3api put-bucket-lifecycle-configuration \
    --bucket "$BUCKET_NAME" \
    --lifecycle-configuration "file://${SCRIPT_DIR}/s3-lifecycle.json"
echo "✓ Lifecycle rules applied"

echo ""
echo "Applying CORS configuration..."
aws s3api put-bucket-cors \
    --bucket "$BUCKET_NAME" \
    --cors-configuration "file://${SCRIPT_DIR}/s3-cors.json"
echo "✓ CORS configuration applied"

echo ""
echo "Enabling server-side encryption (AES256)..."
aws s3api put-bucket-encryption \
    --bucket "$BUCKET_NAME" \
    --server-side-encryption-configuration '{
        "Rules": [{
            "ApplyServerSideEncryptionByDefault": {
                "SSEAlgorithm": "AES256"
            },
            "BucketKeyEnabled": true
        }]
    }'
echo "✓ Encryption enabled"

echo ""
echo "=============================================="
echo "S3 Bucket Setup Complete!"
echo "=============================================="
echo ""
echo "Bucket ARN: arn:aws:s3:::$BUCKET_NAME"
echo ""
echo "Next steps:"
echo "  1. Create IAM user and policy (see below)"
echo "  2. Add credentials to your .env file"
echo ""
echo "=============================================="
echo "IAM Setup Commands"
echo "=============================================="
echo ""

# Generate the policy with correct bucket name
POLICY_JSON=$(cat "${SCRIPT_DIR}/iam-policy.json" | sed "s/BUCKET_NAME_HERE/$BUCKET_NAME/g")

echo "# Create the IAM policy:"
echo "aws iam create-policy \\"
echo "    --policy-name $IAM_POLICY_NAME \\"
echo "    --policy-document '$POLICY_JSON'"
echo ""
echo "# Create IAM user:"
echo "aws iam create-user --user-name $IAM_USER_NAME"
echo ""
echo "# Attach policy to user (replace ACCOUNT_ID with your AWS account ID):"
echo "aws iam attach-user-policy \\"
echo "    --user-name $IAM_USER_NAME \\"
echo "    --policy-arn arn:aws:iam::ACCOUNT_ID:policy/$IAM_POLICY_NAME"
echo ""
echo "# Create access keys:"
echo "aws iam create-access-key --user-name $IAM_USER_NAME"
echo ""
echo "=============================================="
echo "Environment Variables to Add"
echo "=============================================="
echo ""
echo "# Add to your .env file:"
echo "AWS_ACCESS_KEY_ID=<from create-access-key output>"
echo "AWS_SECRET_ACCESS_KEY=<from create-access-key output>"
echo "AWS_REGION=$REGION"
echo "S3_FILESHARE_BUCKET=$BUCKET_NAME"
echo ""
