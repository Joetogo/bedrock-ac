PROJECT ?= neat-graph-bedrock
REGION  ?= us-east-1
STACK   ?= $(PROJECT)

.PHONY: layer build deploy gateway secrets test clean

layer:
	bash scripts/build_layer.sh

build: layer
	cd infra && sam build --template template.yaml

deploy: build
	cd infra && sam deploy \
		--stack-name $(STACK) \
		--region $(REGION) \
		--resolve-s3 \
		--capabilities CAPABILITY_NAMED_IAM \
		--parameter-overrides ProjectName=$(PROJECT) \
		--no-confirm-changeset

gateway:
	python scripts/deploy_gateway.py --stack $(STACK) --region $(REGION)

# Populate the two secrets created by the stack. Edit values first.
secrets:
	@echo "aws secretsmanager put-secret-value --secret-id $(PROJECT)/neat-pulse  --secret-string '{\"org_id\":\"...\",\"api_key\":\"...\"}'"
	@echo "aws secretsmanager put-secret-value --secret-id $(PROJECT)/graph-app   --secret-string '{\"tenant_id\":\"...\",\"client_id\":\"...\",\"client_secret\":\"...\"}'"

test:
	python -m pytest tests/ -q

clean:
	rm -rf build infra/.aws-sam

WEBSTACK    ?= $(PROJECT)-web
RUNTIME_ARN ?= arn:aws:bedrock-agentcore:us-east-1:<account_id>:runtime/<runtime-id>
ORIGIN      ?= *

.PHONY: webapp-deploy webapp-build webapp-sync

# Deploy API + storage + auth + hosting. Pass POOL_ID=<existing UserPoolId>.
webapp-deploy:
	cd webapp/infra && sam build --template template.yaml && sam deploy \
		--stack-name $(WEBSTACK) --region $(REGION) --resolve-s3 \
		--capabilities CAPABILITY_IAM \
		--parameter-overrides ProjectName=$(PROJECT) \
			ExistingUserPoolId=$(POOL_ID) RuntimeArn=$(RUNTIME_ARN) \
			AllowedOrigin=$(ORIGIN) \
		--no-confirm-changeset

webapp-build:
	cd webapp/frontend && npm install && npm run build

# Upload the static export and invalidate CloudFront. Pass BUCKET= and DIST_ID= (stack outputs).
webapp-sync:
	aws s3 sync webapp/frontend/out s3://$(BUCKET)/ --delete --region $(REGION)
	aws cloudfront create-invalidation --distribution-id $(DIST_ID) --paths "/*"
