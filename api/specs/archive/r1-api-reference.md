# RuckusONE API Reference

Documented endpoints based on implementation and testing.

**Base URL**: `https://api.ruckus.cloud` (US), `https://api.eu.ruckus.cloud` (EU), `https://api.asia.ruckus.cloud` (ASIA)

**Authentication**: OAuth 2.0 Client Credentials
- Endpoint: `POST /oauth2/token/{tenant_id}`
- Headers: `x-rks-tenantid` for tenant context

---

## Venues

### Get Venues
```http
GET /venues
Headers:
  Authorization: Bearer {token}
  x-rks-tenantid: {tenant_id}  // For MSP querying specific EC

Response: List of venue objects
```

### Get Venue APs
```http
GET /venues/{venue_id}/aps
```

### Get AP Groups
```http
GET /venues/apGroups
Headers:
  x-rks-tenantid: {tenant_id}  // Optional: filter by tenant

Response: List of AP group objects
Fields: id, name, venueId, isDefault, etc.
```

---

## WiFi Networks (WLANs)

### Query WiFi Networks
```http
POST /wifiNetworks/query
Headers:
  Authorization: Bearer {token}
  x-rks-tenantid: {tenant_id}
  Content-Type: application/json

Body:
{
  "fields": [
    "name", "ssid", "description", "nwSubType", "vlan", "vlanPool",
    "captiveType", "id", "isOweMaster", "owePairNetworkId",
    "dsaeOnboardNetwork", "venueApGroups", "cog"
  ],
  "sortField": "name",
  "sortOrder": "ASC"
}

Response:
{
  "fields": [...],
  "totalCount": 7,
  "page": 1,
  "data": [
    {
      "name": "Guest WiFi",
      "id": "abc123...",
      "ssid": "Guest",
      "vlan": 100,
      "nwSubType": "guest",
      "captiveType": "GuestPass",
      "venueApGroups": [...]
    }
  ]
}
```

---

## Access Points

### Query APs
```http
POST /venues/aps/query
Headers:
  Authorization: Bearer {token}
  x-rks-tenantid: {tenant_id}

Body:
{
  "fields": ["name", "serialNumber", "mac", "model", "status", ...],
  "sortField": "name",
  "sortOrder": "ASC"
}

Response: Paginated list of AP objects
```

---

## MSP Operations

### Get MSP End Customers
```http
GET /msp/ecs
Headers:
  Authorization: Bearer {token}

Response: List of EC (End Customer) objects
```

### Get MSP Entitlements
```http
GET /msp/entitlements
```

### Get MSP Admins
```http
GET /msp/admins
```

---

## Notes

### Field Types
- **nwSubType**: `open`, `aaa`, `guest`, `dpsk`, etc.
- **captiveType**: `GuestPass`, `Social`, `SMS`, etc.
- **venueApGroups**: Array of venue/AP group associations

### Pagination
Most query endpoints support pagination:
- `page`: Page number
- `pageSize`: Items per page
- Response includes `totalCount`

### Common Headers
- `Authorization`: Bearer token from OAuth flow
- `x-rks-tenantid`: Override tenant context (for MSP)
- `Content-Type`: `application/json`

---

## Updates

**Last Updated**: 2025-11-24
**Discovered Through**: Implementation testing and API exploration
**Source**: Ruckus Networks RuckusONE API (no official public OpenAPI spec available)
