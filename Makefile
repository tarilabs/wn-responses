IMAGE ?= quay.io/mmortari/wn-responses
TAG   ?= latest

ROUTE_SUFFIX ?= $(shell openssl rand -hex 4)

.PHONY: build push clean deploy

build:
	podman build --platform linux/arm64,linux/amd64 \
		--manifest $(IMAGE):$(TAG) .

push:
	podman manifest push $(IMAGE):$(TAG)

clean:
	-podman manifest rm $(IMAGE):$(TAG)

deploy:
	oc new-project wn-responses 2>/dev/null || true
	kubectl apply -k deploy/ -n wn-responses
	SUFFIX=$(ROUTE_SUFFIX) envsubst < deploy/route.yaml | kubectl apply -n wn-responses -f -
	@echo "Route deployed with suffix: $(ROUTE_SUFFIX)"
	kubectl get routes -n wn-responses
