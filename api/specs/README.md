# API Specifications

This directory contains OpenAPI specifications for external APIs we integrate with.

## RuckusONE API

- `r1-openapi.json` - RuckusONE OpenAPI 3.0 specification
- Updated via: `npm run fetch-r1-spec` or `./scripts/fetch-r1-openapi.sh`

## Usage

### Fetch Latest Spec
```bash
npm run fetch-r1-spec
```

### Generate TypeScript Types (Future)
```bash
npm run generate-types
```

### Validate API Calls (Future)
Use the spec to validate requests/responses in development.
