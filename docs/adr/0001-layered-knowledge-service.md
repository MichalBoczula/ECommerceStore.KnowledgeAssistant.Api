# ADR-0001: Layered Python application service

Status: Accepted

Use the API/Application/Domain/Infrastructure boundaries from ECommerceStoreUsers.
Keep a small application service with injected retrieval/planning protocols. HTTP,
provider configuration and dependency lifecycle belong to the composition root.
Domain and application code do not depend on transport or cloud SDKs. Keep the existing
ASGI and CLI entry points stable. No persistence changes or new application database.

The full FastAPI OpenAPI artifact is the consumer contract. The Web repository owns
Kiota-generated clients and handwritten feature mappers. Errors use the shared store
Problem Details shape; binding failures are 400, provider unavailability and unverified
answers are 503, and unexpected bugs are 500. This replaces the first draft's ad hoc
errorCode/message/requestId response and default framework errors.

Use layer-specific Python tests, import boundary checks and HTTP/OpenAPI assertions.
Integration with cloud providers still requires configured Azure resources.
