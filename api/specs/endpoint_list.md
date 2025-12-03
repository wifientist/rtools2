# Ruckus ONE API Endpoints

Generated: 2025-12-02T18:45:31.630749

Total APIs: 30
Total Endpoints: 1095

---

## Activities API (v0.0.1)

**Base URL:** `http://localhost`

**Endpoint Count:** 5

### View Activities

| Method | Path | Summary |
|--------|------|----------|
| `POST` | `/activities/query` | Get Activities |
| `GET` | `/activities/{activityId}` | Get Activity Details |
| `POST` | `/activities/{activityId}/devices/query` | Get Activity Devices |
| `PUT` | `/activities/{activityId}/notifications` | Update Activity Notification Options |
| `DELETE` | `/private/activities/async-cleanup` |  |

---

## Adaptive Policy Management (v0.0.9)

**Base URL:** `https://api.ruckus.cloud`

**Endpoint Count:** 29

### Policies

| Method | Path | Summary |
|--------|------|----------|
| `GET` | `/policyTemplates/{templateId}/policies` | Get Policies for Template |
| `POST` | `/policyTemplates/{templateId}/policies` | Create Policy |
| `POST` | `/policyTemplates/{templateId}/policies/query` | Query Policies for Template |
| `DELETE` | `/policyTemplates/{templateId}/policies/{policyId}` | Delete Policy |
| `GET` | `/policyTemplates/{templateId}/policies/{policyId}` | Get Policy |
| `PATCH` | `/policyTemplates/{templateId}/policies/{policyId}` | Update Policy |

### Policy Conditions

| Method | Path | Summary |
|--------|------|----------|
| `GET` | `/policyTemplates/{templateId}/policies/{policyId}/conditions` | Get Conditions |
| `POST` | `/policyTemplates/{templateId}/policies/{policyId}/conditions` | Create Condition |
| `DELETE` | `/policyTemplates/{templateId}/policies/{policyId}/conditions/{conditionId}` | Delete Conditions |
| `GET` | `/policyTemplates/{templateId}/policies/{policyId}/conditions/{conditionId}` | Get Condition |
| `PATCH` | `/policyTemplates/{templateId}/policies/{policyId}/conditions/{conditionId}` | Update Policy Condition |

### Policy Set Assignments

| Method | Path | Summary |
|--------|------|----------|
| `GET` | `/policySets/{policySetId}/assignments` | Get Policy Set Assignments |
| `POST` | `/policySets/{policySetId}/assignments/query` | Query Policy Set Assignments |
| `GET` | `/policySets/{policySetId}/assignments/{assignmentId}` | Get Policy Set Assignment |

### Policy Sets

| Method | Path | Summary |
|--------|------|----------|
| `POST` | `/policySets` | Create Policy Set |
| `POST` | `/policySets/query` | Query Policy Sets |
| `DELETE` | `/policySets/{policySetId}` | Delete Policy Set |
| `GET` | `/policySets/{policySetId}` | Get Policy Set |
| `PATCH` | `/policySets/{policySetId}` | Update Policy Set |

### Policy Templates

| Method | Path | Summary |
|--------|------|----------|
| `POST` | `/policyTemplates/policies/query` | Query Policies Across Templates |
| `POST` | `/policyTemplates/query` | Query Policy Templates |
| `GET` | `/policyTemplates/{templateId}` | Get Policy Template |
| `GET` | `/policyTemplates/{templateId}/attributes` | Get Template Attributes |
| `POST` | `/policyTemplates/{templateId}/attributes/query` | Query Template Attributes |
| `GET` | `/policyTemplates/{templateId}/attributes/{attributeId}` | Get Template Attribute |

### Prioritized Policies

| Method | Path | Summary |
|--------|------|----------|
| `GET` | `/policySets/{policySetId}/prioritizedPolicies` | Get Prioritized Policies |
| `DELETE` | `/policySets/{policySetId}/prioritizedPolicies/{policyId}` | Remove Policy from Policy Set |
| `GET` | `/policySets/{policySetId}/prioritizedPolicies/{policyId}` | Get Prioritized Policy |
| `PUT` | `/policySets/{policySetId}/prioritizedPolicies/{policyId}` | Assign Policy to Policy Set |

---

## Admin Enrollment REST API (v0.0.1)

**Base URL:** `http://localhost:8080`

**Rate Limit:** # RateLimit

**Endpoint Count:** 4

### Admin Enrollment API

| Method | Path | Summary |
|--------|------|----------|
| `POST` | `/enrollments/query` | Query Enrollments |
| `GET` | `/enrollments/{enrollmentId}` | Get Enrollments for a Specific Enrollment Identifier |

### Admin Enrollment Registration API

| Method | Path | Summary |
|--------|------|----------|
| `POST` | `/enrollments/registrations/query` | Query Enrollment Registrations |
| `GET` | `/enrollments/registrations/{enrollmentRegistrationId}` | Get Details for a Specific Enrollment RegistrationIdentifier |

---

## Certificate Template API (v0.0.1)

**Base URL:** `https://api.asia.ruckus.cloud`

**Endpoint Count:** 54

### Certificate Authority

| Method | Path | Summary |
|--------|------|----------|
| `POST` | `/certificateAuthorities` | Create New Certificate Authority |
| `POST` | `/certificateAuthorities/query` | Search for Certificate Authorities Matching Search String in Paged Result |
| `DELETE` | `/certificateAuthorities/{caId}` | Delete the Certificate Authority |
| `GET` | `/certificateAuthorities/{caId}` | Get Specific Certificate Authority |
| `PATCH` | `/certificateAuthorities/{caId}` | Update the Certificate Authority |
| `POST` | `/certificateAuthorities/{caId}` | Download the Private KEY of Certificate Authority |
| `GET` | `/certificateAuthorities/{caId}/chains` | Download the Certificate Chain of Certificate Authority |
| `DELETE` | `/certificateAuthorities/{caId}/privateKeys` | Delete the Certificate Authority Private KEY |
| `POST` | `/certificateAuthorities/{caId}/privateKeys` | Upload the Certificate Authority Private KEY |
| `POST` | `/certificateAuthorities/{caId}/subCas` | Create New Sub Certificate Authority |
| `POST` | `/certificateAuthorities/{caId}/subCas/query` | Search for Sub Certificate Authorities Matching Search String in Paged Result |
| `POST` | `/certificateAuthorities/{caId}/templates` | Create New Certificate Template Belonging to a Specific Certificate Authority |
| `POST` | `/certificateAuthorities/{caId}/templates/query` | Search for Templates Belonging to a Specific Certificate Authority That Match Search String in Paged Result |
| `GET` | `/radiusProfiles/{radiusProfileId}/certificateAuthorities` | Get Certificate Authorities Associated with RADIUS |

### Certificate Template

| Method | Path | Summary |
|--------|------|----------|
| `POST` | `/certificateTemplates/query` | Search for Certificate Templates Matching Search String in Paged Result |
| `DELETE` | `/certificateTemplates/{templateId}` | Delete the Certificate Template |
| `GET` | `/certificateTemplates/{templateId}` | Get Specific Certificate Template |
| `PATCH` | `/certificateTemplates/{templateId}` | Update the Certificate Template |
| `GET` | `/certificateTemplates/{templateId}/msiPackages` | Get Microsoft Software Installer Packages of Template |
| `POST` | `/certificateTemplates/{templateId}/msiPackages` | Create Microsoft Software Installer Package for Template |
| `DELETE` | `/certificateTemplates/{templateId}/msiPackages/{msiPackageId}` | Delete the Microsoft Software Installer Package |
| `GET` | `/certificateTemplates/{templateId}/msiPackages/{msiPackageId}` | Get Specific Microsoft Software Installer Package |
| `PATCH` | `/certificateTemplates/{templateId}/msiPackages/{msiPackageId}` | Update the Microsoft Software Installer Package of Template |
| `GET` | `/certificateTemplates/{templateId}/notifications` | Get Notifications of Template |
| `POST` | `/certificateTemplates/{templateId}/notifications` | Create Notification for Template |
| `DELETE` | `/certificateTemplates/{templateId}/notifications/{notificationId}` | Delete the Notification |
| `GET` | `/certificateTemplates/{templateId}/notifications/{notificationId}` | Get Specific Notification |
| `PATCH` | `/certificateTemplates/{templateId}/notifications/{notificationId}` | Update the Notification of Template |
| `DELETE` | `/certificateTemplates/{templateId}/policySets/{policySetId}` | Remove Policy Set from a Template |
| `PUT` | `/certificateTemplates/{templateId}/policySets/{policySetId}` | Update Policy Set for a Template |
| `GET` | `/certificateTemplates/{templateId}/scepKeys` | Get Simple Certificate Enrollment Protocol of Template |
| `POST` | `/certificateTemplates/{templateId}/scepKeys` | Create Simple Certificate Enrollment Protocol for Template |
| `DELETE` | `/certificateTemplates/{templateId}/scepKeys/{scepKeyId}` | Delete the Simple Certificate Enrollment Protocol |
| `GET` | `/certificateTemplates/{templateId}/scepKeys/{scepKeyId}` | Get Specific Simple Certificate Enrollment Protocol of Template |
| `PATCH` | `/certificateTemplates/{templateId}/scepKeys/{scepKeyId}` | Update the Simple Certificate Enrollment Protocol of Template |

### Device Certificate

| Method | Path | Summary |
|--------|------|----------|
| `POST` | `/certificateTemplates/certificates/query` | Search for Certificates Matching Search String in Paged Result |
| `POST` | `/certificateTemplates/{templateId}/certificates` | Generate Certificate |
| `POST` | `/certificateTemplates/{templateId}/certificates/query` | Search for Certificates in Specific Template Matching Search String in Paged Result |
| `GET` | `/certificateTemplates/{templateId}/certificates/{certId}` | Get Specific Certificate |
| `PATCH` | `/certificateTemplates/{templateId}/certificates/{certId}` | Update the Certificate |
| `POST` | `/certificateTemplates/{templateId}/certificates/{certId}` | Download the Private KEY of Certificate |
| `GET` | `/certificateTemplates/{templateId}/certificates/{certId}/chains` | Download Issued Certificate Chain |
| `POST` | `/certificateTemplates/{templateId}/identities/{identityId}/certificates` | Generate Certificate to a Specific Identity |
| `POST` | `/certificateTemplates/{templateId}/identities/{identityId}/certificates/query` | Search for Certificates Associated with Identity in Paged Result |

### Server and Client Certificate

| Method | Path | Summary |
|--------|------|----------|
| `POST` | `/certificateAuthorities/{caId}/certificates` | Create Certificate |
| `POST` | `/certificateAuthorities/{caId}/certificates/query` | Search for Certificates Issued by Specific Certificate Authority Matching Search String in Paged Result |
| `POST` | `/certificates` | Upload the Certificate |
| `POST` | `/certificates/query` | Search for Certificates Matching Search String in Paged Result |
| `DELETE` | `/certificates/{certId}` | Delete a Certificate |
| `GET` | `/certificates/{certId}` | Get Specific Certificate |
| `PATCH` | `/certificates/{certId}` | Update the Certificate |
| `POST` | `/certificates/{certId}` | Download the Private KEY of Certificate |
| `GET` | `/certificates/{certId}/chains` | Download Certificate Chain |
| `GET` | `/radiusProfiles/{radiusProfileId}/certificates` | Get Certificates Associated with RADIUS |

---

## Config Template Service API (v1.0.0)

**Base URL:** `https://api.ruckus.cloud`

**Endpoint Count:** 6

### Configuration Template

| Method | Path | Summary |
|--------|------|----------|
| `POST` | `/templates/{templateId}/dependencies/query` | Query Template Dependency |
| `PUT` | `/templates/{templateId}/enforcementSettings` | Set Template Enforcement Settings |
| `POST` | `/templates/{templateId}/instances/query` | Query Drift Instances |
| `POST` | `/templates/{templateId}/tenants/{tenantId}` | Apply Template |
| `GET` | `/templates/{templateId}/tenants/{tenantId}/diffReports` | Retrieve Diff Reports |
| `PATCH` | `/templates/{templateId}/tenants/{tenantId}/diffReports` | Sync Template |

---

## DPSK Service (v0.0.3)

**Base URL:** `http://localhost:8080`

**Endpoint Count:** 30

### APIs for DPSK Passphrase Device Management

| Method | Path | Summary |
|--------|------|----------|
| `DELETE` | `/dpskServices/{poolId}/passphrases/{passphraseId}/devices` | Delete Devices Associated with a Specific Passphrase |
| `GET` | `/dpskServices/{poolId}/passphrases/{passphraseId}/devices` | Get Devices for a Specific Passphrase |
| `POST` | `/dpskServices/{poolId}/passphrases/{passphraseId}/devices` | Create Devices for a Specific Passphrase |

### APIs for DPSK Passphrase Management

| Method | Path | Summary |
|--------|------|----------|
| `DELETE` | `/dpskServices/{poolId}/passphrases` | Delete Passphrase |
| `GET` | `/dpskServices/{poolId}/passphrases` | Get Passphrase |
| `PATCH` | `/dpskServices/{poolId}/passphrases` | Update Specific DPSK Passphrases |
| `POST` | `/dpskServices/{poolId}/passphrases` | Create DPSK Passphrase |
| `POST` | `/dpskServices/{poolId}/passphrases/csvFiles` | Import Passphrase from CSV |
| `POST` | `/dpskServices/{poolId}/passphrases/query` | Query Passphrases for Specified Pool |
| `POST` | `/dpskServices/{poolId}/passphrases/query/csvFiles` | DPSK Passphrase to CSV |
| `GET` | `/dpskServices/{poolId}/passphrases/{id}` | Get Specific DPSK Passphrase |
| `PATCH` | `/dpskServices/{poolId}/passphrases/{id}` | Update Specific DPSK Passphrase |
| `PUT` | `/dpskServices/{poolId}/passphrases/{id}` | Update Specific DPSK Passphrase |

### APIs for DPSK Service Management

| Method | Path | Summary |
|--------|------|----------|
| `POST` | `/dpskServices/query` | Search for DPSK Pools Matching Search String in Paged Result |
| `DELETE` | `/dpskServices/{poolId}` | Delete the DPSK Pool |
| `GET` | `/dpskServices/{poolId}` | Get Specific DPSK Pool |
| `PATCH` | `/dpskServices/{poolId}` | Update the DPSK Pool |
| `PUT` | `/dpskServices/{poolId}` | Update the DPSK Pool |
| `DELETE` | `/dpskServices/{poolId}/policySets/{policySetId}` | Remove Policy Set from a DPSK Pool |
| `PUT` | `/dpskServices/{poolId}/policySets/{policySetId}` | Update Policy Set for a DPSK Pool |
| `POST` | `/identityGroups/{identityGroupId}/dpskServices` | Create New DPSK Pool |

### APIs for DPSK Service Template Management

| Method | Path | Summary |
|--------|------|----------|
| `POST` | `/templates/dpskServices` | Create New DPSK Pool Template |
| `POST` | `/templates/dpskServices/query` | Search for DPSK Pool Templates Matching Search String in Paged Result |
| `DELETE` | `/templates/dpskServices/{poolTemplateId}` | Delete the DPSK Pool Template |
| `GET` | `/templates/dpskServices/{poolTemplateId}` | Get Specific DPSK Pool Template |
| `PATCH` | `/templates/dpskServices/{poolTemplateId}` | Update the DPSK Pool Template |
| `PUT` | `/templates/dpskServices/{poolTemplateId}` | Update the DPSK Pool Template |
| `POST` | `/templates/dpskServices/{poolTemplateId}/cloneSettings` | Clone the DPSK Pool Template |
| `POST` | `/templates/identityGroups/{identityGroupId}/dpskServices` | Create New DPSK Pool Template with Identity Group |
| `GET` | `/templates/wifiNetworks/{networkTemplateId}/dpskServices` | Get DPSK Pool Templates by Network Template |

---

## Device Enrollment REST API (v0.0.1)

**Base URL:** `http://localhost:8080`

**Rate Limit:** # RateLimit

**Endpoint Count:** 8

### Certificate Download API

| Method | Path | Summary |
|--------|------|----------|
| `GET` | `/deviceEnrollments/workflows/{workflowId}/certificates` | Downloads the Certificate |

### Device Enrollment API

| Method | Path | Summary |
|--------|------|----------|
| `GET` | `/deviceEnrollments/workflows/{workflowId}/enrollments/{enrollmentId}` | Get Enrollment |

### Enrollment File Management API

| Method | Path | Summary |
|--------|------|----------|
| `GET` | `/deviceEnrollments/workflows/{workflowId}/files/{fileId}` | Get Signed URL for Download |

### Workflow Configuration API

| Method | Path | Summary |
|--------|------|----------|
| `GET` | `/deviceEnrollments/workflows/{workflowId}` | Gets Workflow Configuration |

### Workflow Login API

| Method | Path | Summary |
|--------|------|----------|
| `POST` | `/deviceEnrollments/workflows/{workflowId}/login` | Logs in to Workflow |

### Workflow Step API

| Method | Path | Summary |
|--------|------|----------|
| `GET` | `/deviceEnrollments/workflows/{workflowId}/steps/currentSteps` | Gets the Current Workflow Step  |
| `GET` | `/deviceEnrollments/workflows/{workflowId}/steps/{stepId}` | Gets the Workflow Step  |
| `PUT` | `/deviceEnrollments/workflows/{workflowId}/steps/{stepId}` | Updates a Workflow Step |

---

## Entitlement Assignment Endpoints (v0.2.0)

**Base URL:** `http://localhost`

**Endpoint Count:** 9

### Manage Entitlements

| Method | Path | Summary |
|--------|------|----------|
| `DELETE` | `/assignments` | Revoke multiple assignments |
| `GET` | `/assignments` | Retrieve Assignments |
| `PATCH` | `/assignments` | Replace multiple assignments |
| `POST` | `/assignments` | Create multiple assignments |
| `GET` | `/assignments/summaries` | Retrieve Device Type Summary |
| `GET` | `/licenseUsageReports` | Get Entitlement Usage Report |
| `GET` | `/mspBanners` | Retrieve MSP Banner Data |
| `GET` | `/mspEntitlements` | MSP Entitlements |
| `GET` | `/mspEntitlements/summaries` | Refresh MSP Entitlements |

---

## Events and Alarms API (v0.0.3)

**Base URL:** `http://localhost`

**Endpoint Count:** 9

### Alarm

| Method | Path | Summary |
|--------|------|----------|
| `POST` | `/alarms/metas/query` | Get Alarms Venue,AP and Network Data |
| `POST` | `/alarms/query` | Get Alarms |
| `PATCH` | `/alarms/{alarmId}` | Clear Alarm |

### Event

| Method | Path | Summary |
|--------|------|----------|
| `GET` | `/events/adminGroups/{adminGroupId}/latestLogins` | Get Admin Members Last Logins |
| `POST` | `/events/csvFiles` | Export Events Within a Date Range |
| `POST` | `/events/details/query` | Get Events Details Like Venue, AP and Network Data |
| `POST` | `/events/metas/query` | Get Events Venue,AP and Network Data |
| `POST` | `/events/query` | Get Events |
| `POST` | `/historicalClients/query` | Get Historical Clients |

### Group Members Last login Event

| Method | Path | Summary |
|--------|------|----------|
| `GET` | `/events/adminGroups/{adminGroupId}/latestLogins` | Get Admin Members Last Logins |

---

## External Auth API (v0.0.1)

**Base URL:** `https://api.asia.ruckus.cloud`

**Rate Limit:** ## Rate Limit

**Endpoint Count:** 9

### SAML Identity Provider

| Method | Path | Summary |
|--------|------|----------|
| `POST` | `/samlIdpProfiles` | Create SAML Identity Provider Profile |
| `DELETE` | `/samlIdpProfiles/{samlIdpProfileId}` | Delete SAML Identity Provider  Profile |
| `GET` | `/samlIdpProfiles/{samlIdpProfileId}` | Get SAML Identity Provider Profile |
| `PATCH` | `/samlIdpProfiles/{samlIdpProfileId}` | Update Partial SAML Identity Provider Profile |
| `PUT` | `/samlIdpProfiles/{samlIdpProfileId}` | Update Entire SAML Identity Provider Profile |
| `DELETE` | `/samlIdpProfiles/{samlIdpProfileId}/encryptionCertificates/{certificateId}` | Deactivate Encryption Certificate |
| `PUT` | `/samlIdpProfiles/{samlIdpProfileId}/encryptionCertificates/{certificateId}` | Activate Encryption Certificate |
| `DELETE` | `/samlIdpProfiles/{samlIdpProfileId}/signingCertificates/{certificateId}` | Deactivate Signing Certificate |
| `PUT` | `/samlIdpProfiles/{samlIdpProfileId}/signingCertificates/{certificateId}` | Activate Signing Certificate |

---

## File service API (v0.2.7)

**Base URL:** `http://localhost`

**Rate Limit:** ## Rate Limit

**Endpoint Count:** 3

### File

| Method | Path | Summary |
|--------|------|----------|
| `POST` | `/files/uploadurls` | Get Upload URL |
| `GET` | `/files/{fileId}` | Get Download URL |
| `GET` | `/files/{fileId}/urls` | Get File Download URL |

---

## Guest API (v1.7.1)

**Base URL:** `https://api.asia.ruckus.cloud`

**Rate Limit:** ## Rate Limit

**Endpoint Count:** 21

### Guest User

| Method | Path | Summary |
|--------|------|----------|
| `PATCH` | `/wifiNetworks/{wifiNetworkId}/guestUsers` | Guest User Action |
| `POST` | `/wifiNetworks/{wifiNetworkId}/guestUsers` | Add Guest User |
| `DELETE` | `/wifiNetworks/{wifiNetworkId}/guestUsers/{guestUserId}` | Remove Guest User by ID |
| `GET` | `/wifiNetworks/{wifiNetworkId}/guestUsers/{guestUserId}` | Retrieve Guest User by ID |
| `PATCH` | `/wifiNetworks/{wifiNetworkId}/guestUsers/{guestUserId}` | Update Guest User |

### Portal Service Profile

| Method | Path | Summary |
|--------|------|----------|
| `POST` | `/portalServiceProfiles` | Add Portal Service Profile |
| `DELETE` | `/portalServiceProfiles/{portalServiceProfileId}` | Remove Portal Service Profile |
| `GET` | `/portalServiceProfiles/{portalServiceProfileId}` | Retrieve Portal Service Profile |
| `PUT` | `/portalServiceProfiles/{portalServiceProfileId}` | Update Portal Service Profile |
| `PUT` | `/portalServiceProfiles/{portalServiceProfileId}/backgroundImages` | Update Portal Service Profile Background Image |
| `PUT` | `/portalServiceProfiles/{portalServiceProfileId}/logos` | Update Portal Service Profile Logo |
| `PUT` | `/portalServiceProfiles/{portalServiceProfileId}/photos` | Update Portal Service Profile Photo |
| `PUT` | `/portalServiceProfiles/{portalServiceProfileId}/poweredImages` | Update Portal Service Profile Powered Image |

### Portal Service Template

| Method | Path | Summary |
|--------|------|----------|
| `POST` | `/templates/portalServiceProfiles` | Add Portal Service Profile Template |
| `DELETE` | `/templates/portalServiceProfiles/{portalServiceProfileId}` | Remove Portal Service Profile Template |
| `GET` | `/templates/portalServiceProfiles/{portalServiceProfileId}` | Retrieve Portal Service Profile Template |
| `PUT` | `/templates/portalServiceProfiles/{portalServiceProfileId}` | Update Portal Service Profile Template |
| `PUT` | `/templates/portalServiceProfiles/{portalServiceProfileId}/backgroundImages` | Update Portal Service Profile Template Background Image |
| `PUT` | `/templates/portalServiceProfiles/{portalServiceProfileId}/logos` | Update Portal Service Profile Template Logo |
| `PUT` | `/templates/portalServiceProfiles/{portalServiceProfileId}/photos` | Update Portal Service Profile Template Photo |
| `PUT` | `/templates/portalServiceProfiles/{portalServiceProfileId}/poweredImages` | Update Portal Service Profile Template Powered Image |

---

## Identity Management (v0.0.2)

**Base URL:** `https://api.ruckus.cloud`

**Endpoint Count:** 15

### External Identity

| Method | Path | Summary |
|--------|------|----------|
| `POST` | `/externalIdentities/query` | Query External Identities |

### Identity

| Method | Path | Summary |
|--------|------|----------|
| `GET` | `/identities` | Returns Identities in All Groups |
| `POST` | `/identities/query` | Query Identities |
| `GET` | `/identityGroups/{groupId}/identities` | Returns Identities in the Group |
| `GET` | `/identityGroups/{groupId}/identities/{id}` | Returns the Identity |
| `PUT` | `/identityGroups/{groupId}/identities/{id}/venues/{venueId}/ethernetPorts` | Update the Identity's Ethernet Ports |
| `DELETE` | `/identityGroups/{groupId}/identities/{id}/vnis` | Retry VNI Allocation for Identity |

### Identity Group

| Method | Path | Summary |
|--------|------|----------|
| `GET` | `/identityGroups` | Returns the Identity Groups |
| `POST` | `/identityGroups/csvFile` | Export Identity Groups Into File |
| `POST` | `/identityGroups/query` | Query the Identity Groups |
| `GET` | `/identityGroups/{id}` | Returns the Specific Identity Group |
| `PUT` | `/identityGroups/{id}/dpskPools/{dpskPoolId}` | Update the DPSK Pool Association |
| `PUT` | `/identityGroups/{id}/macRegistrationPools/{poolId}` | Update the MAC Registration Association |
| `DELETE` | `/identityGroups/{id}/policySets/{policySetId}` | Remove the Policy Set Association |
| `PUT` | `/identityGroups/{id}/policySets/{policySetId}` | Update the Policy Set Association |

---

## MAC Registration API (v0.0.1)

**Base URL:** `http://localhost:8080`

**Endpoint Count:** 18

### Assign Identity Group

| Method | Path | Summary |
|--------|------|----------|
| `POST` | `/identityGroups/{identityGroupId}/macRegistrationPools` | Create a Registration Pool with Identity Group |

### MAC Registration

| Method | Path | Summary |
|--------|------|----------|
| `DELETE` | `/macRegistrationPools/{poolId}/registrations` | Delete the Specific MAC Registrations |
| `GET` | `/macRegistrationPools/{poolId}/registrations` | MAC Registrations in the Specified Registration Pool |
| `POST` | `/macRegistrationPools/{poolId}/registrations` | Create a MAC Registration in the Specified Registration Pool |
| `POST` | `/macRegistrationPools/{poolId}/registrations/csvFile` | Import MAC Registrations with the Specified Registration Pool |
| `POST` | `/macRegistrationPools/{poolId}/registrations/query` | Search for MAC Registrations with the Specified Criteria |
| `DELETE` | `/macRegistrationPools/{poolId}/registrations/{id}` | Delete the Specific MAC Registration |
| `GET` | `/macRegistrationPools/{poolId}/registrations/{id}` | Returns the Specific MAC Registration |
| `PATCH` | `/macRegistrationPools/{poolId}/registrations/{id}` | Update Properties in the Specific MAC Registration |

### Registration Pool

| Method | Path | Summary |
|--------|------|----------|
| `GET` | `/macRegistrationPools` | Registration Pools That Contain MAC Registrations |
| `POST` | `/macRegistrationPools` | Create a Registration Pool |
| `POST` | `/macRegistrationPools/query` | Search for Registration Pools with the Specified Criteria |
| `DELETE` | `/macRegistrationPools/{id}` | Delete the Specific Registration Pool |
| `GET` | `/macRegistrationPools/{id}` | Returns the Specific Registration Pool Containing MAC Registrations |
| `PATCH` | `/macRegistrationPools/{id}` | Update Properties in the Specific Registration Pool |
| `DELETE` | `/macRegistrationPools/{id}/policySets/{policySetId}` | Delete Policy Set from a Registration Pool |
| `PUT` | `/macRegistrationPools/{id}/policySets/{policySetId}` | Update Policy Set for a Registration Pool |

### Wifi Network

| Method | Path | Summary |
|--------|------|----------|
| `GET` | `/wifiNetworks/{networkId}/macRegistrationPools` | Get MAC Registration Pools by Network |

---

## MSP Services (v0.3.3)

**Base URL:** `https://api.ruckus.cloud`

**Endpoint Count:** 27

### Admin Delegation Management

| Method | Path | Summary |
|--------|------|----------|
| `PATCH` | `/adminDelegations` | Assign Administrators |
| `GET` | `/tenants/{tenantId}/adminDelegations` | Retrieve Active Relationships Between Designated Administrators and Their Assigned Tenants |
| `PUT` | `/tenants/{tenantId}/adminDelegations` | Update Active Relationships Between Designated Administrators and Their Managed Tenant |
| `GET` | `/tenants/{tenantId}/admins` | Retrieve Administrators |
| `DELETE` | `/tenants/{tenantId}/admins/{adminId}` | Revoke Administrator |
| `GET` | `/tenants/{tenantId}/admins/{adminId}` | Retrieve Administrator |
| `PUT` | `/tenants/{tenantId}/admins/{adminId}` | Assign Administrator |

### Brand Settings

| Method | Path | Summary |
|--------|------|----------|
| `GET` | `/brandings` | Retrieve Brand Settings |
| `POST` | `/brandings` | Add Brand Settings |
| `PUT` | `/brandings` | Update Brand Settings |
| `POST` | `/logoFiles` | Update Brand Logos |
| `GET` | `/logoFiles/{fileId}` | Retrieve Brand Logos |

### Deactivation

| Method | Path | Summary |
|--------|------|----------|
| `DELETE` | `/tenantActivations/{tenantId}` | Deactivate |

### Disable Support Team Assistance

| Method | Path | Summary |
|--------|------|----------|
| `DELETE` | `/tenantActivations/supportStatus/{tenantId}` | Disable Support Team Assistance |

### Enable Support Team Assistance

| Method | Path | Summary |
|--------|------|----------|
| `PUT` | `/tenantActivations/supportStatus/{tenantId}` | Enable Support Team Assistance |

### Manage Firmware Upgrade Schedules

| Method | Path | Summary |
|--------|------|----------|
| `POST` | `/firmwareUpgradeSchedules` | Update Firmware Upgrade Schedules |

### Managed-Tenant Operations

| Method | Path | Summary |
|--------|------|----------|
| `POST` | `/tenants` | Add Managed Tenants |
| `DELETE` | `/tenants/{tenantId}` | Remove Managed Tenant |
| `GET` | `/tenants/{tenantId}` | Retrieve Managed Tenant |
| `PUT` | `/tenants/{tenantId}` | Update Managed Tenant |

### Reactivation

| Method | Path | Summary |
|--------|------|----------|
| `PUT` | `/tenantActivations/{tenantId}` | Reactivate |

### Reactivation and Deactivation

| Method | Path | Summary |
|--------|------|----------|
| `DELETE` | `/tenantActivations/{tenantId}` | Deactivate |
| `PUT` | `/tenantActivations/{tenantId}` | Reactivate |

### Resend email Invitation

| Method | Path | Summary |
|--------|------|----------|
| `PUT` | `/tenants/{tenantId}/invitations` | Resend Email Invitation |

### Support Team Assistance

| Method | Path | Summary |
|--------|------|----------|
| `DELETE` | `/tenantActivations/supportStatus/{tenantId}` | Disable Support Team Assistance |
| `PUT` | `/tenantActivations/supportStatus/{tenantId}` | Enable Support Team Assistance |

### Support Team assistance

| Method | Path | Summary |
|--------|------|----------|
| `GET` | `/tenantActivations/supportStatus/{tenantId}` | Retrieve Support Team Assistance Status |

### Tenant Delegation Management

| Method | Path | Summary |
|--------|------|----------|
| `PATCH` | `/tenantDelegations` | Add Designated Accounts to Manage Tenants |
| `GET` | `/tenants/{tenantId}/tenantDelegations` | Retrieve Active Relationships Between Designated Accounts and Their Managed Tenant |
| `PUT` | `/tenants/{tenantId}/tenantDelegations` | Update Active Relationships Between Designated Accounts and Their Managed Tenant |

### tenant-device-query-controller

| Method | Path | Summary |
|--------|------|----------|
| `POST` | `/tenants/devices/apCounts/query` |  |

---

## Manage Entitlements API (v0.2.0)

**Base URL:** `https://api.devalto.ruckuswireless.com`

**Endpoint Count:** 3

### Entitlement

| Method | Path | Summary |
|--------|------|----------|
| `GET` | `/banners` | Get Banners |
| `GET` | `/entitlements` | Get Entitlements |
| `GET` | `/entitlements/summaries` | Get Entitlement Summaries |

---

## Message Template API (v0.0.12)

**Base URL:** `http://localhost:8080`

**Endpoint Count:** 8

### Manage Templates

| Method | Path | Summary |
|--------|------|----------|
| `GET` | `/templateScopes/{templateScopeId}/templates` | Retrieve All Templates in Scope |
| `GET` | `/templateScopes/{templateScopeId}/templates/{genericTemplateId}` | Retrieve Template |

### Registrations

| Method | Path | Summary |
|--------|------|----------|
| `GET` | `/templateScopes/{templateScopeId}/registrations` | Retrieve All Registrations |
| `GET` | `/templateScopes/{templateScopeId}/registrations/{registrationId}` | Retrieve Registration |

### Template Registrations

| Method | Path | Summary |
|--------|------|----------|
| `GET` | `/templateScopes/{templateScopeId}/templates/{templateId}/registrations` | Retrieve a Template's Registrations |

### Template Scope

| Method | Path | Summary |
|--------|------|----------|
| `GET` | `/templateScopes` | Retrieve All Template Scopes |
| `GET` | `/templateScopes/{templateScopeId}` | Get Template Scope |

### Variables

| Method | Path | Summary |
|--------|------|----------|
| `GET` | `/templateScopes/{templateScopeId}/variables` | Retrieve Template Scope Variables |

---

## Policy Management API (v0.0.3)

**Base URL:** `http://localhost:8080`

**Rate Limit:** ## Rate Limit

**Endpoint Count:** 1

### Criteria Evaluation

| Method | Path | Summary |
|--------|------|----------|
| `POST` | `/policySets/{policySetId}/evaluationReports` | Evaluate Criteria |

---

## Property Management (v1.0.1)

**Base URL:** `http://localhost:8080`

**Endpoint Count:** 23

### Property Configuration

| Method | Path | Summary |
|--------|------|----------|
| `POST` | `/venues/propertyConfigs/query` | Query Property Configurations |
| `GET` | `/venues/{venueId}/propertyConfigs` | Get Property Configuration |
| `PATCH` | `/venues/{venueId}/propertyConfigs` | Selectively Update Property Configuration |
| `PUT` | `/venues/{venueId}/propertyConfigs` | Update Property Configuration |
| `PUT` | `/venues/{venueId}/propertyConfigs/residentPortalAssignments/{residentPortalId}` | Update Resident Portal Assignment |

### QoS Profile Assignment API

| Method | Path | Summary |
|--------|------|----------|
| `PUT` | `/venues/{venueId}/units/qosProfileAssignments/{qosProfileId}` | Update QoS Profile Assignment |

### Resident Portals

| Method | Path | Summary |
|--------|------|----------|
| `POST` | `/residentPortals` | Adds Resident Portal |
| `POST` | `/residentPortals/query` | Query Resident Portals |
| `DELETE` | `/residentPortals/{portalId}` | Delete Resident Portal |
| `GET` | `/residentPortals/{portalId}` | Gets Resident Portal |
| `PATCH` | `/residentPortals/{portalId}` | Updates Resident Portal Configurations |
| `DELETE` | `/residentPortals/{portalId}/files/{type}` | Deletes Resident Portal File |
| `GET` | `/residentPortals/{portalId}/files/{type}` | Gets Resident Portal File |

### Units API

| Method | Path | Summary |
|--------|------|----------|
| `GET` | `/venues/{venueId}/units` | Gets Units for Venue |
| `POST` | `/venues/{venueId}/units` | Adds Unit to Venue |
| `POST` | `/venues/{venueId}/units/notifications` | Gets Notifications for Units |
| `POST` | `/venues/{venueId}/units/query` | Query Units for Venue |
| `DELETE` | `/venues/{venueId}/units/{unitId}` | Delete Unit for Venue |
| `GET` | `/venues/{venueId}/units/{unitId}` | Gets Unit for Venue |
| `PATCH` | `/venues/{venueId}/units/{unitId}` | Selectively Updates Unit Configurations |

### Units Identity API

| Method | Path | Summary |
|--------|------|----------|
| `POST` | `/venues/{venueId}/units/identities/query` | Query Associated Identities for a Unit |
| `DELETE` | `/venues/{venueId}/units/{unitId}/identities/{identityId}` | Delete Unit Identity  |
| `PUT` | `/venues/{venueId}/units/{unitId}/identities/{identityId}` | Associate Identity to Unit |

---

## Property Management REST API (v1.0.1)

**Base URL:** `http://localhost:8080`

**Endpoint Count:** 23

### Property Configuration

| Method | Path | Summary |
|--------|------|----------|
| `POST` | `/venues/propertyConfigs/query` | Query Property Configurations |
| `GET` | `/venues/{venueId}/propertyConfigs` | Get Property Configuration |
| `PATCH` | `/venues/{venueId}/propertyConfigs` | Selectively Update Property Configuration |
| `PUT` | `/venues/{venueId}/propertyConfigs` | Update Property Configuration |
| `PUT` | `/venues/{venueId}/propertyConfigs/residentPortalAssignments/{residentPortalId}` | Update Resident Portal Assignment |

### QoS Profile Assignment API

| Method | Path | Summary |
|--------|------|----------|
| `PUT` | `/venues/{venueId}/units/qosProfileAssignments/{qosProfileId}` | Update QoS Profile Assignment |

### Resident Portals

| Method | Path | Summary |
|--------|------|----------|
| `POST` | `/residentPortals` | Adds Resident Portal |
| `POST` | `/residentPortals/query` | Query Resident Portals |
| `DELETE` | `/residentPortals/{portalId}` | Delete Resident Portal |
| `GET` | `/residentPortals/{portalId}` | Gets Resident Portal |
| `PATCH` | `/residentPortals/{portalId}` | Updates Resident Portal Configurations |
| `DELETE` | `/residentPortals/{portalId}/files/{type}` | Deletes Resident Portal File |
| `GET` | `/residentPortals/{portalId}/files/{type}` | Gets Resident Portal File |

### Units API

| Method | Path | Summary |
|--------|------|----------|
| `GET` | `/venues/{venueId}/units` | Gets Units for Venue |
| `POST` | `/venues/{venueId}/units` | Adds Unit to Venue |
| `POST` | `/venues/{venueId}/units/notifications` | Gets Notifications for Units |
| `POST` | `/venues/{venueId}/units/query` | Query Units for Venue |
| `DELETE` | `/venues/{venueId}/units/{unitId}` | Delete Unit for Venue |
| `GET` | `/venues/{venueId}/units/{unitId}` | Gets Unit for Venue |
| `PATCH` | `/venues/{venueId}/units/{unitId}` | Selectively Updates Unit Configurations |

### Units Identity API

| Method | Path | Summary |
|--------|------|----------|
| `POST` | `/venues/{venueId}/units/identities/query` | Query Associated Identities for a Unit |
| `DELETE` | `/venues/{venueId}/units/{unitId}/identities/{identityId}` | Delete Unit Identity  |
| `PUT` | `/venues/{venueId}/units/{unitId}/identities/{identityId}` | Associate Identity to Unit |

---

## RADIUS Attribute Group Management API (v1.0.8)

**Base URL:** `http://localhost:8080`

**Rate Limit:** ## Rate Limit

**Endpoint Count:** 14

### RADIUS Attribute

| Method | Path | Summary |
|--------|------|----------|
| `GET` | `/radiusAttributes` | Get RADIUS Attributes |
| `POST` | `/radiusAttributes/query` | Get RADIUS Attributes |
| `GET` | `/radiusAttributes/vendors` | Get RADIUS Attribute Vendors |
| `GET` | `/radiusAttributes/{id}` | Get RADIUS Attribute |

### RADIUS Attribute Group

| Method | Path | Summary |
|--------|------|----------|
| `GET` | `/radiusAttributeGroups` | Get RADIUS Attribute Groups |
| `POST` | `/radiusAttributeGroups` | Create RADIUS Attribute Group |
| `POST` | `/radiusAttributeGroups/query` | Get RADIUS Attribute Groups |
| `DELETE` | `/radiusAttributeGroups/{groupId}` | Delete RADIUS Attribute Group |
| `GET` | `/radiusAttributeGroups/{groupId}` | Get RADIUS Attribute Group |
| `PATCH` | `/radiusAttributeGroups/{groupId}` | Update RADIUS Attribute Group |

### RADIUS Attribute Group Assignments

| Method | Path | Summary |
|--------|------|----------|
| `GET` | `/radiusAttributeGroups/{groupId}/assignments` | Get External Assignments |
| `POST` | `/radiusAttributeGroups/{groupId}/assignments` | Create External Assignment |
| `DELETE` | `/radiusAttributeGroups/{groupId}/assignments/{assignmentId}` | Delete External Assignment |
| `GET` | `/radiusAttributeGroups/{groupId}/assignments/{assignmentId}` | Get External Assignment |

---

## RUCKUS Edge API (v1.0.3)

**Base URL:** `https://api.asia.ruckus.cloud`

**Rate Limit:** ## Rate Limit

**Endpoint Count:** 51

### ARP Termination Settings

| Method | Path | Summary |
|--------|------|----------|
| `GET` | `/venues/{venueId}/edgeClusters/{clusterId}/arpTerminationSettings` | Get ARP Termination Settings |
| `PUT` | `/venues/{venueId}/edgeClusters/{clusterId}/arpTerminationSettings` | Update ARP Termination Settings |

### Edge Cluster Configuration

| Method | Path | Summary |
|--------|------|----------|
| `GET` | `/venues/{venueId}/edgeClusters` | Get Edge Clusters |
| `POST` | `/venues/{venueId}/edgeClusters` | Create Edge Cluster |
| `DELETE` | `/venues/{venueId}/edgeClusters/{clusterId}` | Delete a Edge Cluster |
| `GET` | `/venues/{venueId}/edgeClusters/{clusterId}` | Get Edge Cluster |
| `PATCH` | `/venues/{venueId}/edgeClusters/{clusterId}` | Update Edge Cluster |
| `GET` | `/venues/{venueId}/edgeClusters/{clusterId}/networkSettings` | Get Edge Cluster Network |
| `PATCH` | `/venues/{venueId}/edgeClusters/{clusterId}/networkSettings` | Update Edge Cluster Network |

### Edge Compatibility Checking

| Method | Path | Summary |
|--------|------|----------|
| `POST` | `/edgeFeatureSets/query` | Query the Requirement of Edge Features |
| `POST` | `/venues/edgeCompatibilities/query` | Query the Edge Compatibility by Venues or Devices |

### Edge DHCP

| Method | Path | Summary |
|--------|------|----------|
| `POST` | `/edgeDhcpServices` | Create DHCP |
| `POST` | `/edgeDhcpServices/edgeCompatibilities/query` | Query the Edge Compatibility of DHCP Services |
| `DELETE` | `/edgeDhcpServices/{dhcpId}` | Delete DHCP |
| `GET` | `/edgeDhcpServices/{dhcpId}` | Get DHCP |
| `PATCH` | `/edgeDhcpServices/{dhcpId}` | Patch DHCP |
| `PUT` | `/edgeDhcpServices/{dhcpId}` | Update DHCP |
| `DELETE` | `/edgeDhcpServices/{dhcpId}/venues/{venueId}/edgeClusters/{edgeClusterId}` | Deactivate DHCP |
| `PUT` | `/edgeDhcpServices/{dhcpId}/venues/{venueId}/edgeClusters/{edgeClusterId}` | Activate DHCP |

### Edge DNS Configuration

| Method | Path | Summary |
|--------|------|----------|
| `GET` | `/venues/{venueId}/edgeClusters/{clusterId}/edges/{serialNumber}/dnsServers` | Get DNS Configuration |
| `PATCH` | `/venues/{venueId}/edgeClusters/{clusterId}/edges/{serialNumber}/dnsServers` | Update DNS Configuration |

### Edge Device Management

| Method | Path | Summary |
|--------|------|----------|
| `POST` | `/venues/{venueId}/edgeClusters/{clusterId}/edges` | Add Device |
| `DELETE` | `/venues/{venueId}/edgeClusters/{clusterId}/edges/{serialNumber}` | Delete Device |
| `GET` | `/venues/{venueId}/edgeClusters/{clusterId}/edges/{serialNumber}` | Get Device |
| `PATCH` | `/venues/{venueId}/edgeClusters/{clusterId}/edges/{serialNumber}` | Update Device |

### Edge LAG Configuration

| Method | Path | Summary |
|--------|------|----------|
| `POST` | `/venues/{venueId}/edgeClusters/{edgeClusterId}/edges/{serialNumber}/lags` | Create Link Aggregation Group |
| `DELETE` | `/venues/{venueId}/edgeClusters/{edgeClusterId}/edges/{serialNumber}/lags/{lagId}` | Delete Link Aggregation Group |
| `GET` | `/venues/{venueId}/edgeClusters/{edgeClusterId}/edges/{serialNumber}/lags/{lagId}` | Get Link Aggregation Group |
| `PATCH` | `/venues/{venueId}/edgeClusters/{edgeClusterId}/edges/{serialNumber}/lags/{lagId}` | Partial Update Link Aggregation Group |
| `PUT` | `/venues/{venueId}/edgeClusters/{edgeClusterId}/edges/{serialNumber}/lags/{lagId}` | Update Link Aggregation Group |

### Edge LAG Sub-Interface

| Method | Path | Summary |
|--------|------|----------|
| `POST` | `/venues/{venueId}/edgeClusters/{edgeClusterId}/edges/{serialNumber}/lags/{lagId}/subInterfaces` | Create Sub-Interface |
| `DELETE` | `/venues/{venueId}/edgeClusters/{edgeClusterId}/edges/{serialNumber}/lags/{lagId}/subInterfaces/{subInterfaceId}` | Delete Sub-Interface |
| `GET` | `/venues/{venueId}/edgeClusters/{edgeClusterId}/edges/{serialNumber}/lags/{lagId}/subInterfaces/{subInterfaceId}` | Get Sub-Interface |
| `PATCH` | `/venues/{venueId}/edgeClusters/{edgeClusterId}/edges/{serialNumber}/lags/{lagId}/subInterfaces/{subInterfaceId}` | Partial Update Sub-Interface |

### Edge Multicast DNS Profile

| Method | Path | Summary |
|--------|------|----------|
| `POST` | `/edgeMulticastDnsProxyProfiles` | Create Multicast DNS Profile |
| `DELETE` | `/edgeMulticastDnsProxyProfiles/{multicastDnsProxyProfileId}` | Delete Multicast DNS Profile |
| `GET` | `/edgeMulticastDnsProxyProfiles/{multicastDnsProxyProfileId}` | Get Multicast DNS Profile |
| `PUT` | `/edgeMulticastDnsProxyProfiles/{multicastDnsProxyProfileId}` | Update Multicast DNS Profile |
| `DELETE` | `/edgeMulticastDnsProxyProfiles/{multicastDnsProxyProfileId}/venues/{venueId}/edgeClusters/{edgeClusterId}` | Deactivate Multicast DNS |
| `PUT` | `/edgeMulticastDnsProxyProfiles/{multicastDnsProxyProfileId}/venues/{venueId}/edgeClusters/{edgeClusterId}` | Activate Multicast DNS |

### Edge Port Configuration

| Method | Path | Summary |
|--------|------|----------|
| `GET` | `/venues/{venueId}/edgeClusters/{clusterId}/edges/{serialNumber}/portConfigs` | Get Physical Port Configuration |
| `PATCH` | `/venues/{venueId}/edgeClusters/{clusterId}/edges/{serialNumber}/portConfigs` | Update Physical Port Configuration |

### Edge Static Route Configuration

| Method | Path | Summary |
|--------|------|----------|
| `GET` | `/venues/{venueId}/edgeClusters/{clusterId}/edges/{serialNumber}/staticRouteConfigs` | Get Static Route Configuration |
| `PATCH` | `/venues/{venueId}/edgeClusters/{clusterId}/edges/{serialNumber}/staticRouteConfigs` | Update Static Route Configuration |

### Edge Sub-Interface Configuration

| Method | Path | Summary |
|--------|------|----------|
| `GET` | `/venues/{venueId}/edgeClusters/{clusterId}/edges/{serialNumber}/ports/{portId}/subInterfaces` | Get Sub-Interfaces |
| `POST` | `/venues/{venueId}/edgeClusters/{clusterId}/edges/{serialNumber}/ports/{portId}/subInterfaces` | Create Multiple Sub-Interfaces |
| `DELETE` | `/venues/{venueId}/edgeClusters/{clusterId}/edges/{serialNumber}/ports/{portId}/subInterfaces/{subInterfaceId}` | Delete Sub-Interface |
| `PATCH` | `/venues/{venueId}/edgeClusters/{clusterId}/edges/{serialNumber}/ports/{portId}/subInterfaces/{subInterfaceId}` | Update Sub-Interface |

### Edge Troubleshooting

| Method | Path | Summary |
|--------|------|----------|
| `PATCH` | `/venues/{venueId}/edgeClusters/{clusterId}/edges/{serialNumber}/hostDetails` | Trigger Edge Action |

### Tunnel Profile Configuration

| Method | Path | Summary |
|--------|------|----------|
| `DELETE` | `/venues/{venueId}/edgeClusters/{clusterId}/tunnelProfiles/{tunnelProfileId}` | Deactivate Edge Cluster in Tunnel Profile |
| `PUT` | `/venues/{venueId}/edgeClusters/{clusterId}/tunnelProfiles/{tunnelProfileId}` | Activate Edge Cluster in Tunnel Profile |

---

## Resident Portal API (v0.0.1)

**Base URL:** `http://localhost:8080`

**Rate Limit:** Resident portals are provided to the unit owners to view and update unit details and secrets. The Resident portal APIs support these functions. These provide details on the property, units and dpsk passphrases associated. The APIs also supports updates of contact details and dpsk passphrases. APIs are authenticated using a specific token associated to the unit. These tokens are generated as the unit is created.

**Endpoint Count:** 16

### Resident Portal Configuration

| Method | Path | Summary |
|--------|------|----------|
| `GET` | `/residents/properties/{propertyId}` | Gets Property Details |
| `GET` | `/residents/properties/{propertyId}/files/{type}` | Gets Resident Portal File |
| `GET` | `/residents/properties/{propertyId}/uiConfigurations` | Gets User Interface Configurations |

### Resident Portal Login API

| Method | Path | Summary |
|--------|------|----------|
| `POST` | `/residents/properties/{propertyId}/units/logins` | Access Resident Portal |

### Resident Portal UI Configuration

| Method | Path | Summary |
|--------|------|----------|
| `GET` | `/residents/properties/{propertyId}/access` | Gets Portal Access Details |
| `GET` | `/residents/properties/{propertyId}/files/favicons` | Gets Resident Portal Icon File |
| `GET` | `/residents/properties/{propertyId}/styles` | Gets Resident Portal Styles |

### Resident Portal Unit API

| Method | Path | Summary |
|--------|------|----------|
| `GET` | `/residents/properties/{propertyId}/units` | Gets Unit Details |
| `PUT` | `/residents/properties/{propertyId}/units` | Selectively Updates Unit Configurations |
| `GET` | `/residents/properties/{propertyId}/units/devices` | Gets Unit Devices |
| `DELETE` | `/residents/properties/{propertyId}/units/devices/{deviceId}` | Delete Device |

### Resident Portal Unit Users API

| Method | Path | Summary |
|--------|------|----------|
| `POST` | `/residents/properties/{propertyId}/units/users/query` | Query Unit Users |
| `GET` | `/residents/properties/{propertyId}/units/users/{userId}` | Get Unit User Details for Specific User |
| `PUT` | `/residents/properties/{propertyId}/units/users/{userId}` | Updates Unit User |
| `GET` | `/residents/properties/{propertyId}/units/users/{userId}/devices` | Get Unit User Devices for Specific User |
| `DELETE` | `/residents/properties/{propertyId}/units/users/{userId}/devices/{deviceId}` | Delete Unit User Device |

---

## Switch Service API (v0.4.0)

**Base URL:** `https://api.ruckus.cloud`

**Rate Limit:** ## Rate Limit

**Endpoint Count:** 133

### AAA Server

| Method | Path | Summary |
|--------|------|----------|
| `DELETE` | `/venues/{venueId}/aaaServers` | Delete AAA Servers |
| `POST` | `/venues/{venueId}/aaaServers` | Add AAA Server |
| `POST` | `/venues/{venueId}/aaaServers/query` | Query AAA Servers |
| `DELETE` | `/venues/{venueId}/aaaServers/{aaaServerId}` | Delete AAA Server |
| `GET` | `/venues/{venueId}/aaaServers/{aaaServerId}` | Get AAA Server-Setting |
| `PUT` | `/venues/{venueId}/aaaServers/{aaaServerId}` | Update AAA Server |

### AAA Setting

| Method | Path | Summary |
|--------|------|----------|
| `GET` | `/venues/{venueId}/aaaSettings` | Retrieve AAA Setting |
| `PUT` | `/venues/{venueId}/aaaSettings` | Update AAA Setting |

### Command-Line Interface Template

| Method | Path | Summary |
|--------|------|----------|
| `DELETE` | `/cliTemplates` | Delete Command-Line Interface Templates |
| `POST` | `/cliTemplates` | Add Command-Line Interface Template |
| `GET` | `/cliTemplates/examples` | Get Command-Line Interface Template-Examples |
| `POST` | `/cliTemplates/query` | Query Command-Line Interface Templates |
| `DELETE` | `/cliTemplates/{cliTemplateId}` | Delete Command-Line Interface Template |
| `GET` | `/cliTemplates/{cliTemplateId}` | Get Command-Line Interface Template |
| `PUT` | `/cliTemplates/{cliTemplateId}` | Update Command-Line Interface Template |
| `DELETE` | `/venues/{venueId}/cliTemplates/{cliTemplateId}` | Disassociate Command-Line Interface Templates |
| `PUT` | `/venues/{venueId}/cliTemplates/{cliTemplateId}` | Associate Command-Line Interface Templates |

### Configuration History

| Method | Path | Summary |
|--------|------|----------|
| `POST` | `/venues/{venueId}/configHistories/query` | Get Configuration History |
| `POST` | `/venues/{venueId}/switches/{switchId}/configHistDetails/query` | Get Configuration History |
| `GET` | `/venues/{venueId}/switches/{switchId}/transactions/{transactionId}/configHistDetails` | Get Configuration History |
| `POST` | `/venues/{venueId}/transactions/{transactionId}/configHistDetails` | Get Configuration History |

### DHCP Server

| Method | Path | Summary |
|--------|------|----------|
| `PATCH` | `/venues/{venueId}/switches/{switchId}/dhcpServerStates` | Change Switch DHCP Server State |
| `DELETE` | `/venues/{venueId}/switches/{switchId}/dhcpServers` | Delete DHCP Servers |
| `POST` | `/venues/{venueId}/switches/{switchId}/dhcpServers` | Add DHCP Server |
| `POST` | `/venues/{venueId}/switches/{switchId}/dhcpServers/query` | Query DHCP Servers |
| `DELETE` | `/venues/{venueId}/switches/{switchId}/dhcpServers/{dhcpServerId}` | Delete DHCP Server-Setting |
| `GET` | `/venues/{venueId}/switches/{switchId}/dhcpServers/{dhcpServerId}` | Get DHCP Server-Setting |
| `PUT` | `/venues/{venueId}/switches/{switchId}/dhcpServers/{dhcpServerId}` | Update DHCP Server-Setting |

### ICX Switch

| Method | Path | Summary |
|--------|------|----------|
| `POST` | `/venues/{venueId}/deviceRequests` | Create Multiple Device Requests |
| `DELETE` | `/venues/{venueId}/stacks/{stackSwitchSerialNumber}` | Delete Stack Member |
| `DELETE` | `/venues/{venueId}/switches` | Delete ICX Switches |
| `GET` | `/venues/{venueId}/switches` | Retrieve ICX Switches |
| `POST` | `/venues/{venueId}/switches` | Add ICX Switches |
| `DELETE` | `/venues/{venueId}/switches/{switchId}` | Delete ICX Switch |
| `GET` | `/venues/{venueId}/switches/{switchId}` | Get ICX Switch |
| `POST` | `/venues/{venueId}/switches/{switchId}` | Add ICX Switch |
| `PUT` | `/venues/{venueId}/switches/{switchId}` | Update ICX Switch |
| `POST` | `/venues/{venueId}/switches/{switchId}/deviceRequests` | Sync/Reboot ICX Device |
| `PUT` | `/venues/{venueId}/switches/{switchId}/positions` | Update Switch Position |

### Import Switch

| Method | Path | Summary |
|--------|------|----------|
| `POST` | `/venues/{venueId}/switches/importRequests` | Add Switches |
| `GET` | `/venues/{venueId}/switches/importResults` | Get DownloadUrl And Import Result |

### LAG

| Method | Path | Summary |
|--------|------|----------|
| `DELETE` | `/venues/{venueId}/switches/{switchId}/lags` | Delete LAGs |
| `GET` | `/venues/{venueId}/switches/{switchId}/lags` | Get LAGs |
| `POST` | `/venues/{venueId}/switches/{switchId}/lags` | Add LAGs |
| `DELETE` | `/venues/{venueId}/switches/{switchId}/lags/{lagId}` | Delete LAG |
| `GET` | `/venues/{venueId}/switches/{switchId}/lags/{lagId}` | Get LAG |
| `PUT` | `/venues/{venueId}/switches/{switchId}/lags/{lagId}` | Update LAG |

### Profile

| Method | Path | Summary |
|--------|------|----------|
| `POST` | `/switchProfiles` | Add Switch Profile |
| `POST` | `/switchProfiles/query` | Query Switch Profiles |
| `DELETE` | `/switchProfiles/{switchProfileId}` | Delete Switch Profile |
| `GET` | `/switchProfiles/{switchProfileId}` | Get Switch Profile |
| `PUT` | `/switchProfiles/{switchProfileId}` | Update Switch Profile |
| `GET` | `/venues/{venueId}/switchProfiles` | Get Switch Profiles |
| `DELETE` | `/venues/{venueId}/switchProfiles/{switchProfileId}` | Disassociate Switch Profile to Venue |
| `PUT` | `/venues/{venueId}/switchProfiles/{switchProfileId}` | Associate Switch Profile to Venue |

### Profile Template

| Method | Path | Summary |
|--------|------|----------|
| `POST` | `/templates/switchProfiles` | Add Switch Profile Template |
| `POST` | `/templates/switchProfiles/query` | Query Switch Profile Templates |
| `DELETE` | `/templates/switchProfiles/{switchProfileId}` | Delete Switch Profile Template |
| `GET` | `/templates/switchProfiles/{switchProfileId}` | Get Switch Profile Template |
| `PUT` | `/templates/switchProfiles/{switchProfileId}` | Update Switch Profile Template |
| `GET` | `/templates/venues/{venueId}/switchProfiles` | Get Switch Profile Templates |
| `DELETE` | `/templates/venues/{venueId}/switchProfiles/{switchProfileId}` | Disassociate Switch Profile Template |
| `PUT` | `/templates/venues/{venueId}/switchProfiles/{switchProfileId}` | Associate Switch Profile Template |

### Profile VLAN

| Method | Path | Summary |
|--------|------|----------|
| `POST` | `/switchProfiles/{switchProfileId}/vlans` | Add VLAN |
| `GET` | `/venues/{venueId}/switchProfiles/vlans` | Get VLANs |
| `GET` | `/venues/{venueId}/switches/{switchId}/vlanUnions` | Retrieve VLANs |

### Switch Access Control List

| Method | Path | Summary |
|--------|------|----------|
| `GET` | `/venues/{venueId}/switches/{switchId}/aclUnions` | Get ACL Unions |
| `GET` | `/venues/{venueId}/switches/{switchId}/acls` | Get Switch ACLs |
| `POST` | `/venues/{venueId}/switches/{switchId}/acls/query` | Query Switch ACLs |

### Switch Configuration Backup

| Method | Path | Summary |
|--------|------|----------|
| `DELETE` | `/venues/{venueId}/switches/{switchId}/configBackups` | Delete Switch Configuration Backups |
| `GET` | `/venues/{venueId}/switches/{switchId}/configBackups` | Get Switch Configuration Backups |
| `POST` | `/venues/{venueId}/switches/{switchId}/configBackups` | Add Switch Configuration Backup |
| `POST` | `/venues/{venueId}/switches/{switchId}/configBackups/comparisons` | Compare Switch Configuration Backups |
| `POST` | `/venues/{venueId}/switches/{switchId}/configBackups/query` | Retrieve Switch Configuration Backups |
| `DELETE` | `/venues/{venueId}/switches/{switchId}/configBackups/{configBackupId}` | Delete Switch Configuration Backup |
| `GET` | `/venues/{venueId}/switches/{switchId}/configBackups/{configBackupId}` | Get Switch Configuration Backup |
| `PATCH` | `/venues/{venueId}/switches/{switchId}/configBackups/{configBackupId}` | Restore Switch Configuration Backup |
| `GET` | `/venues/{venueId}/switches/{switchId}/configBackups/{configBackupId}/formattedConfigs` | Retrieve Formatted Configuration Backup |

### Switch Firmware Upgrade

| Method | Path | Summary |
|--------|------|----------|
| `GET` | `/switchFirmwares/currentVersions` | Get Current Versions |
| `POST` | `/switchFirmwares/schedules/query` | Get Venues |
| `POST` | `/switchFirmwares/schedules/switches/query` | Get Switches |
| `GET` | `/switchFirmwares/versions/{versionType}` | Get Versions |
| `DELETE` | `/venues/{venueId}/switchFirmwares/schedules` | Delete Upgrade Schedule |
| `POST` | `/venues/{venueId}/switchFirmwares/schedules` | Create Upgrade Schedule |
| `PUT` | `/venues/{venueId}/switchFirmwares/schedules` | Change Upgrade Schedule |

### Switch Ports

| Method | Path | Summary |
|--------|------|----------|
| `POST` | `/venues/{venueId}/switches/portSettings` | Get Ports |
| `PUT` | `/venues/{venueId}/switches/portSettings` | Update Ports |
| `POST` | `/venues/{venueId}/switches/powerCycleRequests` | Power Cycle Port |
| `GET` | `/venues/{venueId}/switches/{switchId}/portSettings` | Get Ports |
| `POST` | `/venues/{venueId}/switches/{switchId}/portSettings` | Get Ports |
| `PUT` | `/venues/{venueId}/switches/{switchId}/portSettings` | Update Port |

### Switch Static Routes

| Method | Path | Summary |
|--------|------|----------|
| `DELETE` | `/venues/{venueId}/switches/{switchId}/staticRoutes` | Delete Static Routes |
| `GET` | `/venues/{venueId}/switches/{switchId}/staticRoutes` | Get Static Routes |
| `POST` | `/venues/{venueId}/switches/{switchId}/staticRoutes` | Add Static Route |
| `DELETE` | `/venues/{venueId}/switches/{switchId}/staticRoutes/{staticRouteId}` | Delete Static Route |
| `GET` | `/venues/{venueId}/switches/{switchId}/staticRoutes/{staticRouteId}` | Get Static Route |
| `PUT` | `/venues/{venueId}/switches/{switchId}/staticRoutes/{staticRouteId}` | Update Static Route |

### Switch VLAN

| Method | Path | Summary |
|--------|------|----------|
| `DELETE` | `/venues/{venueId}/switches/{switchId}/vlans` | Delete Switch VLANs |
| `GET` | `/venues/{venueId}/switches/{switchId}/vlans` | Get VLAN-VE-Ports |
| `POST` | `/venues/{venueId}/switches/{switchId}/vlans` | Add Switch VLAN |
| `POST` | `/venues/{venueId}/switches/{switchId}/vlans/query` | Query Switch VLANs |
| `DELETE` | `/venues/{venueId}/switches/{switchId}/vlans/{vlanId}` | Delete Switch VLAN |
| `GET` | `/venues/{venueId}/switches/{switchId}/vlans/{vlanId}` | Get Switch VLAN |
| `PUT` | `/venues/{venueId}/switches/{switchId}/vlans/{vlanId}` | Update Switch VLAN |
| `GET` | `/venues/{venueId}/vlanUnions` | Get VLANs |
| `POST` | `/venues/{venueId}/vlans` | Add Switch VLANs |
| `POST` | `/venues/{venueId}/vlans/query` | Retrieve Switch VLANs |

### Switch Virtual Ethernet

| Method | Path | Summary |
|--------|------|----------|
| `DELETE` | `/venues/{venueId}/switches/{switchId}/vePorts` | Delete Virtual Ethernet Settings |
| `GET` | `/venues/{venueId}/switches/{switchId}/vePorts` | Retrieve Virtual Ethernet Settings |
| `POST` | `/venues/{venueId}/switches/{switchId}/vePorts` | Add Virtual Ethernet Settings |
| `POST` | `/venues/{venueId}/switches/{switchId}/vePorts/query` | Retrieve Virtual Ethernet Settings |
| `DELETE` | `/venues/{venueId}/switches/{switchId}/vePorts/{vePortId}` | Delete Virtual Ethernet Setting |
| `GET` | `/venues/{venueId}/switches/{switchId}/vePorts/{vePortId}` | Get Virtual Ethernet Setting |
| `PUT` | `/venues/{venueId}/switches/{switchId}/vePorts/{vePortId}` | Update Virtual Ethernet Setting |
| `POST` | `/venues/{venueId}/vePorts/query` | Retrieve Virtual Ethernet Settings |

### Venue Switch Setting

| Method | Path | Summary |
|--------|------|----------|
| `GET` | `/venues/{venueId}/switchSettings` | Get Venue Switch Setting |
| `PUT` | `/venues/{venueId}/switchSettings` | Update Venue Switch Setting |

### Venue Template AAA Server

| Method | Path | Summary |
|--------|------|----------|
| `DELETE` | `/templates/venues/{venueId}/aaaServers` | Delete Venue Template AAA Servers |
| `POST` | `/templates/venues/{venueId}/aaaServers` | Add Venue Template AAA Server |
| `POST` | `/templates/venues/{venueId}/aaaServers/query` | Query Venue Template AAA Servers |
| `DELETE` | `/templates/venues/{venueId}/aaaServers/{aaaServerId}` | Delete Venue Template AAA Server |
| `GET` | `/templates/venues/{venueId}/aaaServers/{aaaServerId}` | Get Venue Template AAA Server |
| `PUT` | `/templates/venues/{venueId}/aaaServers/{aaaServerId}` | Update Venue Template AAA Server |

### Venue Template AAA Setting

| Method | Path | Summary |
|--------|------|----------|
| `GET` | `/templates/venues/{venueId}/aaaSettings` | Retrieve Venue Template AAA Setting |
| `PUT` | `/templates/venues/{venueId}/aaaSettings` | Update Venue Template AAA Setting |

### Venue Template Switch Setting

| Method | Path | Summary |
|--------|------|----------|
| `GET` | `/templates/venues/{venueId}/switchSettings` | Get Venue Template Switch Setting |
| `PUT` | `/templates/venues/{venueId}/switchSettings` | Update Venue Template Switch Setting |

### Web Authentication Page Template

| Method | Path | Summary |
|--------|------|----------|
| `POST` | `/webAuthPageTemplates` | Add Web Authentication Template |
| `POST` | `/webAuthPageTemplates/query` | Query Web Authentication Templates |
| `DELETE` | `/webAuthPageTemplates/{templateId}` | Delete Web Authentication Template |
| `GET` | `/webAuthPageTemplates/{templateId}` | Get Web Authentication Template |
| `PUT` | `/webAuthPageTemplates/{templateId}` | Update Web Authentication Template |
| `GET` | `/webAuthPageTemplates/{templateId}/switches` | Get Web Authentication Template's Switch Info |

---

## Tenant Management (v0.3.0)

**Base URL:** `https://api.ruckus.cloud`

**Rate Limit:** ## Rate Limit

**Endpoint Count:** 31

### Administrator

| Method | Path | Summary |
|--------|------|----------|
| `DELETE` | `/admins` | Delete Administrators |
| `GET` | `/admins` | Get Administrator |
| `POST` | `/admins` | Add Administrator |
| `PUT` | `/admins` | Update Administrator |
| `POST` | `/admins/query` | Get Administrator |
| `DELETE` | `/admins/{adminId}` | Delete Administrator |
| `GET` | `/admins/{adminId}` | Get Administrator |

### Delegation

| Method | Path | Summary |
|--------|------|----------|
| `GET` | `/tenants/delegations` | Get Delegations |
| `POST` | `/tenants/delegations` | Invite VAR |
| `DELETE` | `/tenants/delegations/{delegationId}` | Revoke VAR Delegation |
| `GET` | `/tenants/delegations/{delegationId}` | Get Delegation |
| `PUT` | `/tenants/delegations/{delegationId}` | Respond to Delegation |
| `DELETE` | `/tenants/supportDelegations` | Revoke Access |
| `POST` | `/tenants/supportDelegations` | Grant Access |

### Notification Recipient

| Method | Path | Summary |
|--------|------|----------|
| `DELETE` | `/tenants/notificationRecipients` | Delete Notification Recipient |
| `GET` | `/tenants/notificationRecipients` | Get Notification Recipients |
| `POST` | `/tenants/notificationRecipients` | Add Notification Recipient |
| `POST` | `/tenants/notifications/recipients/query` | Query Notification Recipients |

### Privacy Features

| Method | Path | Summary |
|--------|------|----------|
| `GET` | `/tenants/privacySettings` | Get Configured Privacy Settings |
| `PATCH` | `/tenants/privacySettings` | Add or Update Privacy Settings |
| `POST` | `/tenants/privacySettings` | Clear and Save Privacy Settings |

### Tenant

| Method | Path | Summary |
|--------|------|----------|
| `GET` | `/tenants/betaFeatures` | Get Beta Feature Identifiers |
| `PUT` | `/tenants/betaFeatures` | Update Beta Feature Identifiers |
| `GET` | `/tenants/self` | Get Tenant |
| `PUT` | `/tenants/self` | Update a Tenant |

### User Profile

| Method | Path | Summary |
|--------|------|----------|
| `GET` | `/tenants/accounts` | Get Account |
| `GET` | `/tenants/userProfiles` | Get User Profile |
| `PUT` | `/tenants/userProfiles` | Update User Profile |

### acx-mobile-push-notification-endpoint-controller

| Method | Path | Summary |
|--------|------|----------|
| `POST` | `/tenants/mobilePushNotifications` | Add Mobile Push Notification |

### authentication-controller

| Method | Path | Summary |
|--------|------|----------|
| `GET` | `/tenants/authentications` | Get Authentications |
| `POST` | `/tenants/authentications` | Add Authentication |

---

## Venue Service API (v0.2.8)

**Base URL:** `http://localhost`

**Rate Limit:** ## Rate Limit

**Endpoint Count:** 16

### Floor Plan

| Method | Path | Summary |
|--------|------|----------|
| `GET` | `/venues/{venueId}/floorplans` | Access Floor Plans |
| `POST` | `/venues/{venueId}/floorplans` | Request Floor Plan |
| `POST` | `/venues/{venueId}/floorplans/query` | Query Floor Plans |
| `DELETE` | `/venues/{venueId}/floorplans/{floorPlanId}` | Revoke Floor Plan |
| `GET` | `/venues/{venueId}/floorplans/{floorPlanId}` | Access Floor Plan |
| `PUT` | `/venues/{venueId}/floorplans/{floorPlanId}` | Replace Floor Plan |
| `POST` | `/venues/{venueId}/signurls/uploadurls` | Access Image Upload URL |
| `GET` | `/venues/{venueId}/signurls/{fileId}/urls` | Access Image Download URL |

### Venue

| Method | Path | Summary |
|--------|------|----------|
| `POST` | `/venues` | Request Venue |
| `DELETE` | `/venues/{venueId}` | Revoke Venue by ID |
| `GET` | `/venues/{venueId}` | Access Venue by ID |
| `PUT` | `/venues/{venueId}` | Replace Venue |

### Venue Template

| Method | Path | Summary |
|--------|------|----------|
| `POST` | `/templates/venues` | Create Venue Template |
| `DELETE` | `/templates/venues/{venueTemplateId}` | Delete Venue Template by ID |
| `GET` | `/templates/venues/{venueTemplateId}` | Get Venue Template by ID |
| `PUT` | `/templates/venues/{venueTemplateId}` | Update Venue Template |

---

## ViewModel service API (v1.0.42)

**Base URL:** `http://localhost`

**Endpoint Count:** 60

### Client API

| Method | Path | Summary |
|--------|------|----------|
| `POST` | `/venues/aps/clients/query` | Query AP Clients |

### Edge SD-LAN Status

| Method | Path | Summary |
|--------|------|----------|
| `POST` | `/edgeSdLanServices/query` | Get RUCKUS Edge SD-LANs |

### Network

| Method | Path | Summary |
|--------|------|----------|
| `POST` | `/wifiNetworks/query` | Get Wi-Fi Networks Data |

### Quality of Service

| Method | Path | Summary |
|--------|------|----------|
| `POST` | `/qosStatistics/query` | Get the Statistics of Quality of Service |

### View Client Isolation Profiles

| Method | Path | Summary |
|--------|------|----------|
| `POST` | `/clientIsolationProfiles/query` | Query Client Isolation Profiles |

### View Ethernet Port Profile

| Method | Path | Summary |
|--------|------|----------|
| `POST` | `/ethernetPortProfiles/query` | Get Ethernet Port Profiles |

### View MSP

| Method | Path | Summary |
|--------|------|----------|
| `POST` | `/delegations` | Get Delegations |
| `POST` | `/mspecs/query` | Query MSP-ECs |
| `POST` | `/msps/{mspTenantId}/ecInventories/query` | Get EC Inventory |
| `POST` | `/msps/{mspTenantId}/ecInventories/query/csvFiles` | Export EC Inventory |
| `POST` | `/techpartners/mspecs/query` | Query MSP-ECs for Tech Partner |

### View Platform

| Method | Path | Summary |
|--------|------|----------|
| `POST` | `/venues/query` | Get Venues |
| `GET` | `/venues/{venueId}/aps/models` | Get Venue AP Models |
| `POST` | `/venues/{venueId}/rogueAps/query` | Get Venue Rogue APs |

### View Portal Service Profiles

| Method | Path | Summary |
|--------|------|----------|
| `POST` | `/portalServiceProfiles/query` | Query Portal Service Profiles |

### View Portal Service Template

| Method | Path | Summary |
|--------|------|----------|
| `POST` | `/templates/portalServiceProfiles/query` | Query Portal Service Profile Templates |

### View SNMP Agent Profiles

| Method | Path | Summary |
|--------|------|----------|
| `POST` | `/snmpAgentProfiles/query` | Query SNMP Agent Profiles |

### View SoftGRE Profile

| Method | Path | Summary |
|--------|------|----------|
| `POST` | `/softGreProfiles/query` | Query SoftGRE Profiles |

### View Switch

| Method | Path | Summary |
|--------|------|----------|
| `POST` | `/venues/switches/aggregationDetails` | Get Switches Aggregation Details |
| `POST` | `/venues/switches/clients/query` | Get Switch Clients |
| `POST` | `/venues/switches/query` | Get Switches of Venue |
| `POST` | `/venues/switches/query/csvFiles` | Export Switch Inventory |
| `POST` | `/venues/switches/switchPorts/query` | Query Switch Ports |

### View Template

| Method | Path | Summary |
|--------|------|----------|
| `POST` | `/templates/query` | Get Template List |

### View VLAN Pool Profiles

| Method | Path | Summary |
|--------|------|----------|
| `POST` | `/vlanPoolProfiles/query` | Query VLAN Pool Profiles |

### View Venue

| Method | Path | Summary |
|--------|------|----------|
| `POST` | `/venues/query` | Get Venues |
| `GET` | `/venues/{venueId}/aps/models` | Get Venue AP Models |
| `POST` | `/venues/{venueId}/rogueAps/query` | Get Venue Rogue APs |

### View Venue Template

| Method | Path | Summary |
|--------|------|----------|
| `POST` | `/templates/venues/query` | Get Venue Templates |

### View Venue Topology

| Method | Path | Summary |
|--------|------|----------|
| `GET` | `/venues/{venueId}/meshTopologies` | Get Mesh Topology |
| `GET` | `/venues/{venueId}/topologies` | Get Topology |

### View Wi-Fi

| Method | Path | Summary |
|--------|------|----------|
| `POST` | `/guestUsers/query` | Get Guests |
| `POST` | `/venues/apGroups/query` | Query AP Groups |
| `POST` | `/venues/aps/query` | Get APs |

### View Wi-Fi Profile

| Method | Path | Summary |
|--------|------|----------|
| `POST` | `/accessControlProfiles/query` | Get Access Control Profiles |
| `POST` | `/applicationPolicies/query` | Get Application Policies |
| `POST` | `/devicePolicies/query` | Get Device Policies |
| `POST` | `/dhcpConfigServiceProfiles/query` | Get DHCP Configuration Service Profiles |
| `POST` | `/hotspot20IdentityProviders/query` | Get Hotspot 2.0 Identity Providers |
| `POST` | `/hotspot20Operators/query` | Get Hotspot 2.0 Operators |
| `POST` | `/l2AclPolicies/query` | Get Layer Two Policies |
| `POST` | `/l3AclPolicies/query` | Get Layer Three Policies |
| `POST` | `/lbsServerProfiles/query` | Get Location Based Service Server Profiles |
| `POST` | `/multicastDnsProxyProfiles/query` | Get Multicast DNS Proxy Profiles |
| `POST` | `/radiusServerProfiles/query` | Get RADIUS Server Profiles |
| `POST` | `/roguePolicies/query` | Get Rogue Policies |
| `POST` | `/syslogServerProfiles/query` | Get Syslog Server Profiles |
| `POST` | `/tunnelServiceProfiles/query` | Get Tunnel Profiles |
| `POST` | `/wifiCallingServiceProfiles/query` | Get Wifi Calling Profiles |

### View Wi-Fi Template

| Method | Path | Summary |
|--------|------|----------|
| `POST` | `/templates/accessControlProfiles/query` | Get Access Control Profile Templates |
| `POST` | `/templates/applicationPolicies/query` | Get Application Policy Templates |
| `POST` | `/templates/devicePolicies/query` | Get Device Policy Templates |
| `POST` | `/templates/dhcpConfigServiceProfiles/query` | Get DHCP Configuration Service Profile Templates |
| `POST` | `/templates/ethernetPortProfiles/query` | Get Ethernet Port Profile Templates |
| `POST` | `/templates/l2AclPolicies/query` | Get Layer Two Policy Templates |
| `POST` | `/templates/l3AclPolicies/query` | Get Layer Three Policy Templates |
| `POST` | `/templates/radiusServerProfiles/query` | Get RADIUS Server Profile Templates |
| `POST` | `/templates/roguePolicies/query` | Get Rogue Policy Templates |
| `POST` | `/templates/syslogServerProfiles/query` | Get Syslog Server Profile Templates |
| `POST` | `/templates/venues/apGroups/query` | Get AP Group Templates |
| `POST` | `/templates/vlanPoolProfiles/query` | Query VLAN Pool Profile Templates |
| `POST` | `/templates/wifiCallingServiceProfiles/query` | Get Wifi Calling Profile Templates |
| `POST` | `/templates/wifiNetworks/query` | Get Wi-Fi Network Template data |

---

## WiFi API (v17.3.3.205)

**Base URL:** `https://api.asia.ruckus.cloud`

**Rate Limit:** ## Rate Limit

**Endpoint Count:** 432

### AP

| Method | Path | Summary |
|--------|------|----------|
| `POST` | `/venues/{venueId}/apGroups/{apGroupId}/aps` | Add AP with AP Group |
| `PUT` | `/venues/{venueId}/apGroups/{apGroupId}/aps/{serialNumber}` | Move AP Into AP Group |
| `POST` | `/venues/{venueId}/aps` | Add AP or Import APs |
| `GET` | `/venues/{venueId}/aps/importResults` | Get Import Venue APs Results |
| `DELETE` | `/venues/{venueId}/aps/{serialNumber}` | Delete AP |
| `GET` | `/venues/{venueId}/aps/{serialNumber}` | Get AP |
| `PUT` | `/venues/{venueId}/aps/{serialNumber}` | Update AP |
| `GET` | `/venues/{venueId}/aps/{serialNumber}/antennaTypeSettings` | Get AP Antenna Type |
| `PUT` | `/venues/{venueId}/aps/{serialNumber}/antennaTypeSettings` | Update AP Antenna Type |
| `GET` | `/venues/{venueId}/aps/{serialNumber}/bssColoringSettings` | Get AP Basic Service Set Coloring Settings |
| `PUT` | `/venues/{venueId}/aps/{serialNumber}/bssColoringSettings` | Update AP Basic Service Set Coloring Settings |
| `GET` | `/venues/{venueId}/aps/{serialNumber}/capabilities` | Get AP Capabilities |
| `GET` | `/venues/{venueId}/aps/{serialNumber}/dhcpSettings` | Get AP DHCP Settings |
| `PATCH` | `/venues/{venueId}/aps/{serialNumber}/diagnosisCommands` | Trigger AP Diagnosis Commands |
| `GET` | `/venues/{venueId}/aps/{serialNumber}/directedMulticastSettings` | Get AP Directed Multicast Settings |
| `PUT` | `/venues/{venueId}/aps/{serialNumber}/directedMulticastSettings` | Update AP Directed Multicast Settings |
| `GET` | `/venues/{venueId}/aps/{serialNumber}/externalAntennaSettings` | Get AP External Antenna Settings |
| `PUT` | `/venues/{venueId}/aps/{serialNumber}/externalAntennaSettings` | Update AP External Antenna Settings |
| `GET` | `/venues/{venueId}/aps/{serialNumber}/iotSettings` | Get AP IoT Settings |
| `PUT` | `/venues/{venueId}/aps/{serialNumber}/iotSettings` | Update AP IoT Settings |
| `GET` | `/venues/{venueId}/aps/{serialNumber}/ledSettings` | Get AP LED |
| `PUT` | `/venues/{venueId}/aps/{serialNumber}/ledSettings` | Update AP LED |
| `GET` | `/venues/{venueId}/aps/{serialNumber}/logs` | Get the AP Log Info |
| `GET` | `/venues/{venueId}/aps/{serialNumber}/managementTrafficVlanSettings` | Get AP Management Traffic VLAN Settings |
| `PUT` | `/venues/{venueId}/aps/{serialNumber}/managementTrafficVlanSettings` | Update AP Management Traffic VLAN Settings |
| `GET` | `/venues/{venueId}/aps/{serialNumber}/meshSettings` | Get AP Mesh Settings |
| `PUT` | `/venues/{venueId}/aps/{serialNumber}/meshSettings` | Update AP Mesh Settings |
| `PATCH` | `/venues/{venueId}/aps/{serialNumber}/neighbors` | Patch AP Neighbors |
| `POST` | `/venues/{venueId}/aps/{serialNumber}/neighbors/query` | Query AP Neighbors |
| `GET` | `/venues/{venueId}/aps/{serialNumber}/networkSettings` | Get AP Network Settings |
| `PUT` | `/venues/{venueId}/aps/{serialNumber}/networkSettings` | Update AP Network Settings |
| `GET` | `/venues/{venueId}/aps/{serialNumber}/packets` | Get AP Packets |
| `PATCH` | `/venues/{venueId}/aps/{serialNumber}/packets` | Patch AP Packets |
| `DELETE` | `/venues/{venueId}/aps/{serialNumber}/pictures` | Delete AP Pictures |
| `GET` | `/venues/{venueId}/aps/{serialNumber}/pictures` | Get AP Pictures |
| `PUT` | `/venues/{venueId}/aps/{serialNumber}/pictures` | Update AP Pictures |
| `GET` | `/venues/{venueId}/aps/{serialNumber}/radioSettings` | Get AP Radio |
| `PUT` | `/venues/{venueId}/aps/{serialNumber}/radioSettings` | Update AP Radio |
| `GET` | `/venues/{venueId}/aps/{serialNumber}/smartMonitorSettings` | Get AP Smart Monitor Settings |
| `PUT` | `/venues/{venueId}/aps/{serialNumber}/smartMonitorSettings` | Update AP Smart Monitor Settings |
| `DELETE` | `/venues/{venueId}/aps/{serialNumber}/stickyClientSteeringSettings` | Reset AP Sticky Client Steering Settings |
| `GET` | `/venues/{venueId}/aps/{serialNumber}/stickyClientSteeringSettings` | Get AP Sticky Client Steering Settings |
| `PUT` | `/venues/{venueId}/aps/{serialNumber}/stickyClientSteeringSettings` | Update AP Sticky Client Steering Settings |
| `PATCH` | `/venues/{venueId}/aps/{serialNumber}/systemCommands` | Trigger AP System Commands |
| `GET` | `/venues/{venueId}/aps/{serialNumber}/usbPortSettings` | Get AP USB Port |
| `PUT` | `/venues/{venueId}/aps/{serialNumber}/usbPortSettings` | Update AP USB Port |
| `GET` | `/venues/{venueId}/aps/{serialNumber}/wifiAvailableChannels` | Get AP Available Channels |
| `DELETE` | `/venues/{venueId}/floorplans/{floorplanId}/aps/{serialNumber}/floorPositions` | Deactivate AP Floor Position |
| `GET` | `/venues/{venueId}/floorplans/{floorplanId}/aps/{serialNumber}/floorPositions` | Get AP Floor Position |
| `PUT` | `/venues/{venueId}/floorplans/{floorplanId}/aps/{serialNumber}/floorPositions` | Activate AP Floor Position |

### AP Compatibility

| Method | Path | Summary |
|--------|------|----------|
| `POST` | `/venues/apCompatibilities/query` | Venue Compatibility Query |
| `POST` | `/venues/aps/apCompatibilities/query` | AP Compatibility Query |
| `POST` | `/wifiFeatureSets/query` | Wi-Fi Feature Sets Query |
| `POST` | `/wifiNetworks/apCompatibilities/query` | Wi-Fi Network Compatibility Query |

### AP Group

| Method | Path | Summary |
|--------|------|----------|
| `POST` | `/venues/{venueId}/apGroups` | Create AP-Group |
| `DELETE` | `/venues/{venueId}/apGroups/{apGroupId}` | Delete AP Group |
| `GET` | `/venues/{venueId}/apGroups/{apGroupId}` | Get AP Group |
| `PUT` | `/venues/{venueId}/apGroups/{apGroupId}` | Update AP Group |
| `DELETE` | `/venues/{venueId}/wifiNetworks/{wifiNetworkId}/apGroups/{apGroupId}` | Deactivate AP Group On Wi-Fi Network |
| `PUT` | `/venues/{venueId}/wifiNetworks/{wifiNetworkId}/apGroups/{apGroupId}` | Activate AP Group On Wi-Fi Network |
| `GET` | `/venues/{venueId}/wifiNetworks/{wifiNetworkId}/apGroups/{apGroupId}/settings` | Get AP Group Settings On Wi-Fi Network |
| `PUT` | `/venues/{venueId}/wifiNetworks/{wifiNetworkId}/apGroups/{apGroupId}/settings` | Update AP Group Settings On Wi-Fi Network |

### AP Group Template

| Method | Path | Summary |
|--------|------|----------|
| `POST` | `/templates/venues/{venueId}/apGroups` | Create AP Group Template |
| `DELETE` | `/templates/venues/{venueId}/apGroups/{apGroupId}` | Delete AP Group Template |
| `GET` | `/templates/venues/{venueId}/apGroups/{apGroupId}` | Get AP Group Template |
| `PUT` | `/templates/venues/{venueId}/apGroups/{apGroupId}` | Update AP Group Template |
| `POST` | `/templates/venues/{venueId}/apGroups/{apGroupId}/cloneSettings` | Clone AP Group Template |
| `DELETE` | `/templates/venues/{venueId}/wifiNetworks/{wifiNetworkId}/apGroups/{apGroupId}` | Deactivate AP Group Template On Wi-Fi Network Template |
| `PUT` | `/templates/venues/{venueId}/wifiNetworks/{wifiNetworkId}/apGroups/{apGroupId}` | Activate AP Group Template On Wi-Fi Network Template |
| `GET` | `/templates/venues/{venueId}/wifiNetworks/{wifiNetworkId}/apGroups/{apGroupId}/settings` | Get AP Group Settings Template On Wi-Fi Network Template |
| `PUT` | `/templates/venues/{venueId}/wifiNetworks/{wifiNetworkId}/apGroups/{apGroupId}/settings` | Update AP Group Settings Template On Wi-Fi Network Template |

### AP Venue

| Method | Path | Summary |
|--------|------|----------|
| `GET` | `/venues/apAvailableLteBands` | Get Available LTE Bands |
| `GET` | `/venues/apModelCapabilities` | Get Venue All AP-Model Capabilities |
| `GET` | `/venues/{venueId}/apBssColoringSettings` | Get Venue Basic Service Set Coloring Settings |
| `PUT` | `/venues/{venueId}/apBssColoringSettings` | Update Venue Basic Service Set Coloring Settings |
| `GET` | `/venues/{venueId}/apCellularSettings` | Get Venue AP-Model Cellular |
| `PUT` | `/venues/{venueId}/apCellularSettings` | Update Venue AP-Model Cellular |
| `GET` | `/venues/{venueId}/apClientAdmissionControlSettings` | Get Venue Client Admission Control Settings |
| `PUT` | `/venues/{venueId}/apClientAdmissionControlSettings` | Update Venue Client Admission Control Settings |
| `GET` | `/venues/{venueId}/apDirectedMulticastSettings` | Get Venue Directed Multicast Settings |
| `PUT` | `/venues/{venueId}/apDirectedMulticastSettings` | Update Venue Directed Multicast Settings |
| `GET` | `/venues/{venueId}/apDosProtectionSettings` | Get Venue DoS Protection |
| `PUT` | `/venues/{venueId}/apDosProtectionSettings` | Update Venue DoS Protection |
| `GET` | `/venues/{venueId}/apLoadBalancingSettings` | Get Venue Load Balancing Settings |
| `PUT` | `/venues/{venueId}/apLoadBalancingSettings` | Update Venue Load Balancing Settings |
| `GET` | `/venues/{venueId}/apManagementTrafficVlanSettings` | Get Venue AP Management VLAN Settings |
| `PUT` | `/venues/{venueId}/apManagementTrafficVlanSettings` | Update Venue AP Management VLAN Settings |
| `GET` | `/venues/{venueId}/apMeshSettings` | Get Mesh Settings |
| `PUT` | `/venues/{venueId}/apMeshSettings` | Update Mesh |
| `GET` | `/venues/{venueId}/apModelAntennaTypeSettings` | Get Venue Antenna Type |
| `PUT` | `/venues/{venueId}/apModelAntennaTypeSettings` | Update Venue Antenna Type |
| `GET` | `/venues/{venueId}/apModelBandModeSettings` | Get Venue Band Mode |
| `PUT` | `/venues/{venueId}/apModelBandModeSettings` | Update Venue Band Mode |
| `GET` | `/venues/{venueId}/apModelCapabilities` | Get Venue AP-Model Capabilities |
| `GET` | `/venues/{venueId}/apModelExternalAntennaSettings` | Get Venue AP Model External Antenna Settings |
| `PUT` | `/venues/{venueId}/apModelExternalAntennaSettings` | Update Venue AP Model External Antenna Settings |
| `GET` | `/venues/{venueId}/apModelLedSettings` | Get Venue LED |
| `PUT` | `/venues/{venueId}/apModelLedSettings` | Update Venue LED |
| `GET` | `/venues/{venueId}/apModelUsbPortSettings` | Get Venue USB Port |
| `PUT` | `/venues/{venueId}/apModelUsbPortSettings` | Update Venue USB Port |
| `GET` | `/venues/{venueId}/apMulticastDnsFencingSettings` | Get Venue Multicast DNS Fencing Settings |
| `PUT` | `/venues/{venueId}/apMulticastDnsFencingSettings` | Update Venue Multicast DNS Fencing Settings |
| `GET` | `/venues/{venueId}/apRadioSettings` | Get Venue Radio |
| `PUT` | `/venues/{venueId}/apRadioSettings` | Update Venue Radio |
| `GET` | `/venues/{venueId}/apRadiusOptions` | Get Venue RADIUS Options Settings |
| `PUT` | `/venues/{venueId}/apRadiusOptions` | Update Venue RADIUS Options Settings |
| `GET` | `/venues/{venueId}/apRebootTimeoutSettings` | Get Venue Reboot Timeout Settings |
| `PUT` | `/venues/{venueId}/apRebootTimeoutSettings` | Update Venue Reboot Timeout Settings |
| `GET` | `/venues/{venueId}/apSmartMonitorSettings` | Get Venue Smart Monitor Settings |
| `PUT` | `/venues/{venueId}/apSmartMonitorSettings` | Update Venue Smart Monitor Settings |
| `GET` | `/venues/{venueId}/apTlsKeyEnhancedSettings` | Get Venue Transport Layer Security KEY Enhanced Mode Settings for APs |
| `PUT` | `/venues/{venueId}/apTlsKeyEnhancedSettings` | Update Venue Transport Layer Security KEY Enhanced Mode Settings for APs |
| `GET` | `/venues/{venueId}/wifiAvailableChannels` | Get Venue Available Channels |

### AP Venue Template

| Method | Path | Summary |
|--------|------|----------|
| `GET` | `/templates/venues/{venueId}/apBssColoringSettings` | Get Venue Template Basic Service Set Coloring Settings |
| `PUT` | `/templates/venues/{venueId}/apBssColoringSettings` | Update Venue Template Basic Service Set Coloring Settings |
| `GET` | `/templates/venues/{venueId}/apCellularSettings` | Get Venue Template AP-Model Cellular |
| `PUT` | `/templates/venues/{venueId}/apCellularSettings` | Update Venue Template AP-Model Cellular |
| `GET` | `/templates/venues/{venueId}/apClientAdmissionControlSettings` | Get Venue Template Client Admission Control Settings |
| `PUT` | `/templates/venues/{venueId}/apClientAdmissionControlSettings` | Update Venue Template Client Admission Control Settings |
| `GET` | `/templates/venues/{venueId}/apDirectedMulticastSettings` | Get Venue Template Directed Multicast Settings |
| `PUT` | `/templates/venues/{venueId}/apDirectedMulticastSettings` | Update Venue Template Directed Multicast Settings |
| `GET` | `/templates/venues/{venueId}/apDosProtectionSettings` | Get Venue Template DoS Protection |
| `PUT` | `/templates/venues/{venueId}/apDosProtectionSettings` | Update Venue Template DoS Protection |
| `GET` | `/templates/venues/{venueId}/apLoadBalancingSettings` | Get Venue Template Load Balancing Settings |
| `PUT` | `/templates/venues/{venueId}/apLoadBalancingSettings` | Update Venue Template Load Balancing Settings |
| `GET` | `/templates/venues/{venueId}/apMeshSettings` | Get Mesh Settings |
| `PUT` | `/templates/venues/{venueId}/apMeshSettings` | Update Mesh |
| `GET` | `/templates/venues/{venueId}/apModelBandModeSettings` | Get Venue Template Band Mode Settings |
| `PUT` | `/templates/venues/{venueId}/apModelBandModeSettings` | Update Template Venue Band Mode Settings |
| `GET` | `/templates/venues/{venueId}/apModelCapabilities` | Get Venue Template AP-Model Capabilities |
| `GET` | `/templates/venues/{venueId}/apModelExternalAntennaSettings` | Get Venue Template AP Model External Antenna Settings |
| `PUT` | `/templates/venues/{venueId}/apModelExternalAntennaSettings` | Update Venue Template AP Model External Antenna Settings |
| `GET` | `/templates/venues/{venueId}/apModelLanPortSettings` | Get Venue Template LAN-Ports |
| `GET` | `/templates/venues/{venueId}/apModelLedSettings` | Get Venue Template LED |
| `PUT` | `/templates/venues/{venueId}/apModelLedSettings` | Update Venue LED |
| `GET` | `/templates/venues/{venueId}/apMulticastDnsFencingSettings` | Get Venue Template Multicast DNS Fencing Settings |
| `PUT` | `/templates/venues/{venueId}/apMulticastDnsFencingSettings` | Update Venue Template Multicast DNS Fencing Settings |
| `GET` | `/templates/venues/{venueId}/apRadioSettings` | Get Venue Template AP Radio Settings |
| `PUT` | `/templates/venues/{venueId}/apRadioSettings` | Update Venue Template Radio Settings |
| `GET` | `/templates/venues/{venueId}/apRadiusOptions` | Get Venue Template RADIUS Options Settings |
| `PUT` | `/templates/venues/{venueId}/apRadiusOptions` | Update Venue Template RADIUS Options Settings |
| `GET` | `/templates/venues/{venueId}/apRebootTimeoutSettings` | Get Venue Template AP Reboot Timeout |
| `PUT` | `/templates/venues/{venueId}/apRebootTimeoutSettings` | Update Venue Template AP Reboot Timeout |
| `GET` | `/templates/venues/{venueId}/apSmartMonitorSettings` | Get Venue Template AP Smart Monitor |
| `PUT` | `/templates/venues/{venueId}/apSmartMonitorSettings` | Update Venue Template AP Smart Monitor |
| `GET` | `/templates/venues/{venueId}/wifiAvailableChannels` | Get Venue Template Available Channels |

### Access Control Profile

| Method | Path | Summary |
|--------|------|----------|
| `POST` | `/accessControlProfiles` | Add Access Control Profile |
| `DELETE` | `/accessControlProfiles/{accessControlProfileId}` | Delete Access Control Profile |
| `GET` | `/accessControlProfiles/{accessControlProfileId}` | Get Access Control Profile |
| `PUT` | `/accessControlProfiles/{accessControlProfileId}` | Update Access Control Profile |
| `DELETE` | `/wifiNetworks/{wifiNetworkId}/accessControlProfiles/{accessControlProfileId}` | Deactivate Access Control Profile On Wi-Fi Network |
| `PUT` | `/wifiNetworks/{wifiNetworkId}/accessControlProfiles/{accessControlProfileId}` | Activate Access Control Profile On Wi-Fi Network |

### Access Control Profile Template

| Method | Path | Summary |
|--------|------|----------|
| `POST` | `/templates/accessControlProfiles` | Add Access Control Profile Template |
| `DELETE` | `/templates/accessControlProfiles/{accessControlProfileTemplateId}` | Delete Access Control Profile Template |
| `GET` | `/templates/accessControlProfiles/{accessControlProfileTemplateId}` | Get Access Control Profile Template |
| `PUT` | `/templates/accessControlProfiles/{accessControlProfileTemplateId}` | Update Access Control Profile Template |
| `DELETE` | `/templates/wifiNetworks/{wifiNetworkTemplateId}/accessControlProfiles/{accessControlProfileTemplateId}` | Deactivate Access Control Profile Template On Wi-Fi Network Template |
| `PUT` | `/templates/wifiNetworks/{wifiNetworkTemplateId}/accessControlProfiles/{accessControlProfileTemplateId}` | Activate Access Control Profile Template On Wi-Fi Network Template |

### Application Library

| Method | Path | Summary |
|--------|------|----------|
| `GET` | `/applicationLibraries/{applicationLibraryId}/categories` | Get Application Library Categories |
| `GET` | `/applicationLibraries/{applicationLibraryId}/categories/{categoryId}/applications` | Get Application Library Applications |
| `GET` | `/applicationLibrarySettings` | Get Application Library Settings |
| `PATCH` | `/applicationLibrarySettings` | Patch Application Library Settings |

### Application Policy

| Method | Path | Summary |
|--------|------|----------|
| `DELETE` | `/accessControlProfiles/{accessControlProfileId}/applicationPolicies/{applicationPolicyId}` | Deactivate Application Policy On Access Control Profile |
| `PUT` | `/accessControlProfiles/{accessControlProfileId}/applicationPolicies/{applicationPolicyId}` | Activate Application Policy On Access Control Profile |
| `POST` | `/applicationPolicies` | Add Application Policy |
| `DELETE` | `/applicationPolicies/{applicationPolicyId}` | Delete Application Policy |
| `GET` | `/applicationPolicies/{applicationPolicyId}` | Get Application Policy |
| `PUT` | `/applicationPolicies/{applicationPolicyId}` | Update Application Policy |
| `DELETE` | `/wifiNetworks/{wifiNetworkId}/applicationPolicies/{applicationPolicyId}` | Deactivate Application Policy On Wifi Network |
| `PUT` | `/wifiNetworks/{wifiNetworkId}/applicationPolicies/{applicationPolicyId}` | Activate Application Policy On Wifi Network |

### Application Policy Template

| Method | Path | Summary |
|--------|------|----------|
| `DELETE` | `/templates/accessControlProfiles/{accessControlProfileTemplateId}/applicationPolicies/{applicationPolicyTemplateId}` | Deactivate Application Policy Template On Access Control Profile Template |
| `PUT` | `/templates/accessControlProfiles/{accessControlProfileTemplateId}/applicationPolicies/{applicationPolicyTemplateId}` | Activate Application Policy Template On Access Control Profile Template |
| `POST` | `/templates/applicationPolicies` | Add Application Policy Template |
| `DELETE` | `/templates/applicationPolicies/{applicationPolicyTemplateId}` | Delete Application Policy Template |
| `GET` | `/templates/applicationPolicies/{applicationPolicyTemplateId}` | Get Application Policy Template |
| `PUT` | `/templates/applicationPolicies/{applicationPolicyTemplateId}` | Update Application Policy Template |

### Certificate Template Activation

| Method | Path | Summary |
|--------|------|----------|
| `DELETE` | `/wifiNetworks/{wifiNetworkId}/certificateTemplates/{certificateTemplateId}` | Deactivate Certificate Template On Wi-Fi Network |
| `PUT` | `/wifiNetworks/{wifiNetworkId}/certificateTemplates/{certificateTemplateId}` | Activate Certificate Template On Wi-Fi Network |

### Client

| Method | Path | Summary |
|--------|------|----------|
| `PATCH` | `/venues/{venueId}/aps/{serialNumber}/clients/{clientMacAddress}` | Patch AP Client |

### Client Isolation Profile

| Method | Path | Summary |
|--------|------|----------|
| `POST` | `/clientIsolationProfiles` | Create Client Isolation Profile |
| `DELETE` | `/clientIsolationProfiles/{clientIsolationProfileId}` | Delete Client Isolation Profile |
| `GET` | `/clientIsolationProfiles/{clientIsolationProfileId}` | Get Client Isolation Profile |
| `PUT` | `/clientIsolationProfiles/{clientIsolationProfileId}` | Update Client Isolation Profile |
| `DELETE` | `/venues/{venueId}/apModels/{apModel}/lanPorts/{portId}/clientIsolationProfiles/{clientIsolationProfileId}` | Deactivate Client Isolation Profile On Venue AP Model LAN Port |
| `PUT` | `/venues/{venueId}/apModels/{apModel}/lanPorts/{portId}/clientIsolationProfiles/{clientIsolationProfileId}` | Activate Client Isolation Profile On Venue AP Model LAN Port |
| `DELETE` | `/venues/{venueId}/aps/{serialNumber}/lanPorts/{portId}/clientIsolationProfiles/{clientIsolationProfileId}` | Deactivate Client Isolation Profile On AP LAN Port |
| `PUT` | `/venues/{venueId}/aps/{serialNumber}/lanPorts/{portId}/clientIsolationProfiles/{clientIsolationProfileId}` | Activate Client Isolation Profile On AP LAN Port |
| `DELETE` | `/venues/{venueId}/wifiNetworks/{wifiNetworkId}/clientIsolationProfiles/{clientIsolationProfileId}` | Deactivate Client Isolation Profile On Wi-Fi Network |
| `PUT` | `/venues/{venueId}/wifiNetworks/{wifiNetworkId}/clientIsolationProfiles/{clientIsolationProfileId}` | Activate Client Isolation Profile On Wi-Fi Network |

### DHCP Configuration Service Profile

| Method | Path | Summary |
|--------|------|----------|
| `POST` | `/dhcpConfigServiceProfiles` | Create DHCP Configuration Service Profile |
| `DELETE` | `/dhcpConfigServiceProfiles/{dhcpConfigServiceProfileId}` | Delete DHCP Configuration Service Profile |
| `GET` | `/dhcpConfigServiceProfiles/{dhcpConfigServiceProfileId}` | Get DHCP Configuration Service Profile |
| `PUT` | `/dhcpConfigServiceProfiles/{dhcpConfigServiceProfileId}` | Update DHCP Configuration Service Profile |
| `GET` | `/venues/{venueId}/aps/{serialNumber}/wifiDhcpClientLeases` | Get AP DHCP Client Leases |
| `GET` | `/venues/{venueId}/aps/{serialNumber}/wifiDhcpPoolUsages` | Get DHCP Pools Usage in This AP |
| `DELETE` | `/venues/{venueId}/dhcpConfigServiceProfiles/{dhcpConfigServiceProfileId}` | Deactivate DHCP Configuration Service Profile On This Venue |
| `GET` | `/venues/{venueId}/dhcpConfigServiceProfiles/{dhcpConfigServiceProfileId}` | Get DHCP Service Profile Settings of This Venue |
| `PUT` | `/venues/{venueId}/dhcpConfigServiceProfiles/{dhcpConfigServiceProfileId}` | Activate DHCP Configuration Service Profile On This Venue and Update Settings |
| `GET` | `/venues/{venueId}/wifiDhcpClientLeases` | Get Venue DHCP Leases |
| `GET` | `/venues/{venueId}/wifiDhcpPoolUsages` | Get DHCP Pools Usage in Venue |

### DHCP Configuration Service Profile Template

| Method | Path | Summary |
|--------|------|----------|
| `POST` | `/templates/dhcpConfigServiceProfiles` | Create DHCP Configuration Service Profile Template |
| `DELETE` | `/templates/dhcpConfigServiceProfiles/{dhcpConfigServiceProfileId}` | Delete DHCP Configuration Service Profile Template |
| `GET` | `/templates/dhcpConfigServiceProfiles/{dhcpConfigServiceProfileId}` | Get DHCP Configuration Service Profile Template |
| `PUT` | `/templates/dhcpConfigServiceProfiles/{dhcpConfigServiceProfileId}` | Update DHCP Configuration Service Profile Template |
| `DELETE` | `/templates/venues/{venueTemplateId}/dhcpConfigServiceProfiles/{dhcpConfigServiceProfileId}` | Deactivate DHCP Configuration Service Profile On Venue Template |
| `GET` | `/templates/venues/{venueTemplateId}/dhcpConfigServiceProfiles/{dhcpConfigServiceProfileId}` | Get DHCP Service Profile Settings of Venue Template |
| `PUT` | `/templates/venues/{venueTemplateId}/dhcpConfigServiceProfiles/{dhcpConfigServiceProfileId}` | Activate DHCP Configuration Service Profile On Venue Template and Update Settings |
| `GET` | `/templates/venues/{venueTemplateId}/wifiDhcpPoolUsages` | Get DHCP Pools Usage in Venue Template |

### DPSK Service

| Method | Path | Summary |
|--------|------|----------|
| `PUT` | `/wifiNetworks/{wifiNetworkId}/dpskServices/{dpskServiceId}` | Activate DPSK Service On Wi-Fi Network |

### DPSK Service Template

| Method | Path | Summary |
|--------|------|----------|
| `PUT` | `/templates/wifiNetworks/{wifiNetworkTemplateId}/dpskServices/{dpskServiceTemplateId}` | Activate DPSK Service Template On Wi-Fi Network Template |

### Device Policy

| Method | Path | Summary |
|--------|------|----------|
| `DELETE` | `/accessControlProfiles/{accessControlProfileId}/devicePolicies/{devicePolicyId}` | Deactivate Device Policy On Access Control Profile |
| `PUT` | `/accessControlProfiles/{accessControlProfileId}/devicePolicies/{devicePolicyId}` | Activate Device Policy On Access Control Profile |
| `POST` | `/devicePolicies` | Create Device-Policy |
| `DELETE` | `/devicePolicies/{devicePolicyId}` | Delete Device-Policy |
| `GET` | `/devicePolicies/{devicePolicyId}` | Get Device-Policy |
| `PUT` | `/devicePolicies/{devicePolicyId}` | Update Device-Policy |
| `DELETE` | `/wifiNetworks/{wifiNetworkId}/devicePolicies/{policyId}` | Deactivate Device Policy On Wi-Fi Network |
| `PUT` | `/wifiNetworks/{wifiNetworkId}/devicePolicies/{policyId}` | Activate Device Policy On Wi-Fi Network |

### Device Policy Template

| Method | Path | Summary |
|--------|------|----------|
| `DELETE` | `/templates/accessControlProfiles/{accessControlProfileTemplateId}/devicePolicies/{devicePolicyTemplateId}` | Deactivate Device Policy Template On Access Control Profile Template |
| `PUT` | `/templates/accessControlProfiles/{accessControlProfileTemplateId}/devicePolicies/{devicePolicyTemplateId}` | Activate Device Policy Template On Access Control Profile Template |
| `POST` | `/templates/devicePolicies` | Create Device-Policy Template |
| `DELETE` | `/templates/devicePolicies/{devicePolicyTemplateId}` | Delete Device-Policy Template |
| `GET` | `/templates/devicePolicies/{devicePolicyTemplateId}` | Get Device-Policy Template |
| `PUT` | `/templates/devicePolicies/{devicePolicyTemplateId}` | Update Device-Policy Template |

### Ethernet Port Profile

| Method | Path | Summary |
|--------|------|----------|
| `POST` | `/ethernetPortProfiles` | Create Ethernet Port Profile |
| `DELETE` | `/ethernetPortProfiles/{ethernetPortProfileId}` | Delete Ethernet Port Profile |
| `GET` | `/ethernetPortProfiles/{ethernetPortProfileId}` | Get Ethernet Port Profile |
| `PUT` | `/ethernetPortProfiles/{ethernetPortProfileId}` | Update Ethernet Port Profile |
| `DELETE` | `/ethernetPortProfiles/{ethernetPortProfileId}/radiusServerProfiles/{radiusServerProfileId}` | Deactivate RADIUS Server Profile On Ethernet Port Profile |
| `PUT` | `/ethernetPortProfiles/{ethernetPortProfileId}/radiusServerProfiles/{radiusServerProfileId}` | Activate RADIUS Server Profile On Ethernet Port Profile |
| `GET` | `/venues/{venueId}/apModels/{apModel}/lanPortSpecificSettings` | Get Venue AP Model LAN Port Specific Settings |
| `PUT` | `/venues/{venueId}/apModels/{apModel}/lanPortSpecificSettings` | Update Venue AP Model LAN Port Specific Settings |
| `DELETE` | `/venues/{venueId}/apModels/{apModel}/lanPorts/{portId}/ethernetPortProfiles/{ethernetPortProfileId}` | Deactivate Ethernet Port Profile On Venue AP Model LAN Port |
| `PUT` | `/venues/{venueId}/apModels/{apModel}/lanPorts/{portId}/ethernetPortProfiles/{ethernetPortProfileId}` | Activate Ethernet Port Profile On Venue AP Model LAN Port |
| `GET` | `/venues/{venueId}/apModels/{apModel}/lanPorts/{portId}/settings` | Get Venue AP Model LAN Port Settings |
| `PUT` | `/venues/{venueId}/apModels/{apModel}/lanPorts/{portId}/settings` | Update Venue AP Model LAN Port Settings |
| `GET` | `/venues/{venueId}/aps/{serialNumber}/lanPortSpecificSettings` | Get AP LAN Port Specific Settings |
| `PUT` | `/venues/{venueId}/aps/{serialNumber}/lanPortSpecificSettings` | Update AP LAN Port Specific Settings |
| `DELETE` | `/venues/{venueId}/aps/{serialNumber}/lanPorts/{portId}/ethernetPortProfiles/{ethernetPortProfileId}` | Deactivate Ethernet Port Profile On AP LAN Port |
| `PUT` | `/venues/{venueId}/aps/{serialNumber}/lanPorts/{portId}/ethernetPortProfiles/{ethernetPortProfileId}` | Activate Ethernet Port Profile On AP LAN Port |
| `GET` | `/venues/{venueId}/aps/{serialNumber}/lanPorts/{portId}/settings` | Get AP LAN Port Settings |
| `PUT` | `/venues/{venueId}/aps/{serialNumber}/lanPorts/{portId}/settings` | Update AP LAN Port Settings |

### Hotspot 2.0 Identity Provider

| Method | Path | Summary |
|--------|------|----------|
| `POST` | `/hotspot20IdentityProviders` | Add Hotspot 2.0 Identity Provider |
| `DELETE` | `/hotspot20IdentityProviders/{hotspot20IdentityProviderId}` | Delete Hotspot 2.0 Identity Provider |
| `GET` | `/hotspot20IdentityProviders/{hotspot20IdentityProviderId}` | Get Hotspot 2.0 Identity Provider |
| `PUT` | `/hotspot20IdentityProviders/{hotspot20IdentityProviderId}` | Update Hotspot 2.0 Identity Provider |
| `DELETE` | `/wifiNetworks/{wifiNetworkId}/hotspot20IdentityProviders/{hotspot20IdentityProviderId}` | Deactivate Hotspot 2.0 Identity Provider On Wi-Fi Network |
| `PUT` | `/wifiNetworks/{wifiNetworkId}/hotspot20IdentityProviders/{hotspot20IdentityProviderId}` | Activate Hotspot 2.0 Identity Provider On Wi-Fi Network |

### Hotspot 2.0 Operator

| Method | Path | Summary |
|--------|------|----------|
| `POST` | `/hotspot20Operators` | Create Hotspot 2.0 Operator |
| `DELETE` | `/hotspot20Operators/{hotspot20OperatorId}` | Delete Hotspot 2.0 Operator |
| `GET` | `/hotspot20Operators/{hotspot20OperatorId}` | Get Hotspot 2.0 Operator |
| `PUT` | `/hotspot20Operators/{hotspot20OperatorId}` | Update Hotspot 2.0 Operator |
| `DELETE` | `/wifiNetworks/{wifiNetworkId}/hotspot20Operators/{hotspot20OperatorId}` | Deactivate Hotspot 2.0 Operator On Wi-Fi Network |
| `PUT` | `/wifiNetworks/{wifiNetworkId}/hotspot20Operators/{hotspot20OperatorId}` | Activate Hotspot 2.0 Operator On Wi-Fi Network |

### L2ACL Policy

| Method | Path | Summary |
|--------|------|----------|
| `DELETE` | `/accessControlProfiles/{accessControlProfileId}/l2AclPolicies/{l2AclPolicyId}` | Deactivate Layer-2 ACL Policy On Access Control Profile |
| `PUT` | `/accessControlProfiles/{accessControlProfileId}/l2AclPolicies/{l2AclPolicyId}` | Activate Layer-2 ACL Policy On Access Control Profile |
| `POST` | `/l2AclPolicies` | Add Layer-2 ACL |
| `DELETE` | `/l2AclPolicies/{l2AclPolicyId}` | Delete Layer-2 ACL |
| `GET` | `/l2AclPolicies/{l2AclPolicyId}` | Get Layer-2 ACL |
| `PUT` | `/l2AclPolicies/{l2AclPolicyId}` | Update Layer-2 ACL |
| `DELETE` | `/templates/accessControlProfiles/{accessControlProfileTemplateId}/l2AclPolicies/{l2AclPolicyTemplateId}` | Deactivate Layer-2 ACL Policy Template On Access Control Profile Template |
| `PUT` | `/templates/accessControlProfiles/{accessControlProfileTemplateId}/l2AclPolicies/{l2AclPolicyTemplateId}` | Activate Layer-2 ACL Policy Template On Access Control Profile Template |
| `POST` | `/templates/l2AclPolicies` | Add Layer-2 ACL Policy Template |
| `DELETE` | `/templates/l2AclPolicies/{l2AclPolicyTemplateId}` | Delete Layer-2 ACL Policy Template |
| `GET` | `/templates/l2AclPolicies/{l2AclPolicyTemplateId}` | Get Layer-2 ACL Template |
| `PUT` | `/templates/l2AclPolicies/{l2AclPolicyTemplateId}` | Update Layer-2 ACL Policy Template |
| `DELETE` | `/wifiNetworks/{wifiNetworkId}/l2AclPolicies/{l2AclPolicyId}` | Deactivate Layer-2 ACL Policy On Wi-Fi Network |
| `PUT` | `/wifiNetworks/{wifiNetworkId}/l2AclPolicies/{l2AclPolicyId}` | Activate Layer-2 ACL Policy On Wi-Fi Network |

### L3ACL Policy

| Method | Path | Summary |
|--------|------|----------|
| `DELETE` | `/accessControlProfiles/{accessControlProfileId}/l3AclPolicies/{l3AclPolicyId}` | Deactivate Layer-3 ACL Policy On Access Control Profile |
| `PUT` | `/accessControlProfiles/{accessControlProfileId}/l3AclPolicies/{l3AclPolicyId}` | Activate Layer-3 ACL Policy On Access Control Profile |
| `POST` | `/l3AclPolicies` | Add Layer-3 ACL |
| `DELETE` | `/l3AclPolicies/{l3AclPolicyId}` | Delete Layer-3 ACL |
| `GET` | `/l3AclPolicies/{l3AclPolicyId}` | Get Layer-3 ACL |
| `PUT` | `/l3AclPolicies/{l3AclPolicyId}` | Update Layer-3 ACL |
| `DELETE` | `/wifiNetworks/{wifiNetworkId}/l3AclPolicies/{l3AclPolicyId}` | Deactivate Layer-3 ACL Policy On Wi-Fi Network |
| `PUT` | `/wifiNetworks/{wifiNetworkId}/l3AclPolicies/{l3AclPolicyId}` | Activate Layer-3 ACL Policy On Wi-Fi Network |

### L3ACL Policy Template

| Method | Path | Summary |
|--------|------|----------|
| `DELETE` | `/templates/accessControlProfiles/{accessControlProfileTemplateId}/l3AclPolicies/{l3AclPolicyTemplateId}` | Deactivate Layer-3 ACL Policy Template On Access Control Profile Template |
| `PUT` | `/templates/accessControlProfiles/{accessControlProfileTemplateId}/l3AclPolicies/{l3AclPolicyTemplateId}` | Activate Layer-3 ACL Policy Template On Access Control Profile Template |
| `POST` | `/templates/l3AclPolicies` | Add Layer-3 ACL Policy Template |
| `DELETE` | `/templates/l3AclPolicies/{l3AclPolicyTemplateId}` | Delete Layer-3 ACL Policy Template |
| `GET` | `/templates/l3AclPolicies/{l3AclPolicyTemplateId}` | Get Layer-3 ACL Template |
| `PUT` | `/templates/l3AclPolicies/{l3AclPolicyTemplateId}` | Update Layer-3 ACL Policy Template |

### LBS Server Profile

| Method | Path | Summary |
|--------|------|----------|
| `POST` | `/lbsServerProfiles` | Create Location Based Service Server Profile |
| `DELETE` | `/lbsServerProfiles/{lbsServerProfileId}` | Delete Location Based Service Server Profile |
| `GET` | `/lbsServerProfiles/{lbsServerProfileId}` | Get Location Based Service Server Profile |
| `PUT` | `/lbsServerProfiles/{lbsServerProfileId}` | Update Location Based Service Server Profile |
| `DELETE` | `/venues/{venueId}/lbsServerProfiles/{lbsServerProfileId}` | Deactivate Location Based Service Server Profile On Venue |
| `PUT` | `/venues/{venueId}/lbsServerProfiles/{lbsServerProfileId}` | Activate Location Based Service Server Profile On Venue |

### MAC Registration Pool

| Method | Path | Summary |
|--------|------|----------|
| `DELETE` | `/wifiNetworks/{wifiNetworkId}/macRegistrationPools/{macRegistrationPoolId}` | Deactivate MAC Registration Pool On Wi-Fi Network |
| `PUT` | `/wifiNetworks/{wifiNetworkId}/macRegistrationPools/{macRegistrationPoolId}` | Activate MAC Registration Pool On Wi-Fi Network |

### MDNS Proxy Service Profile

| Method | Path | Summary |
|--------|------|----------|
| `POST` | `/multicastDnsProxyProfiles` | Create Multicast DNS Proxy Service Profile |
| `DELETE` | `/multicastDnsProxyProfiles/{multicastDnsProxyProfileId}` | Delete Multicast DNS Proxy Service Profile |
| `GET` | `/multicastDnsProxyProfiles/{multicastDnsProxyProfileId}` | Get Multicast DNS Proxy Service Profile |
| `PUT` | `/multicastDnsProxyProfiles/{multicastDnsProxyProfileId}` | Update Multicast DNS Proxy Service Profile |
| `DELETE` | `/venues/{venueId}/aps/{apSerialNumber}/multicastDnsProxyProfiles/{multicastDnsProxyProfileId}` | Deactivate Multicast DNS Proxy Service Profile On the AP |
| `PUT` | `/venues/{venueId}/aps/{apSerialNumber}/multicastDnsProxyProfiles/{multicastDnsProxyProfileId}` | Activate Multicast DNS Proxy Service Profile On the AP |

### Portal Service Profile Template

| Method | Path | Summary |
|--------|------|----------|
| `PUT` | `/templates/wifiNetworks/{wifiNetworkTemplateId}/portalServiceProfiles/{portalServiceProfileTemplateId}` | Activate Portal Service Profile Template On Wi-Fi Network Template |

### RADIUS Profile

| Method | Path | Summary |
|--------|------|----------|
| `DELETE` | `/hotspot20IdentityProviders/{hotspot20IdentityProviderId}/radiusServerProfiles/{radiusId}` | Deactivate RADIUS Server Profile On Hotspot 2.0 Identity Provider |
| `PUT` | `/hotspot20IdentityProviders/{hotspot20IdentityProviderId}/radiusServerProfiles/{radiusId}` | Activate RADIUS Server Profile On Hotspot 2.0 Identity Provider |
| `POST` | `/radiusServerProfiles` | Add RADIUS Profile |
| `DELETE` | `/radiusServerProfiles/{radiusId}` | Delete RADIUS Profile |
| `GET` | `/radiusServerProfiles/{radiusId}` | Get RADIUS Profile |
| `PUT` | `/radiusServerProfiles/{radiusId}` | Update RADIUS Profile |
| `GET` | `/wifiNetworks/{wifiNetworkId}/radiusServerProfileSettings` | Get RADIUS Server Profile Settings On Wi-Fi Network |
| `PUT` | `/wifiNetworks/{wifiNetworkId}/radiusServerProfileSettings` | Update RADIUS Server Profile Settings On Wi-Fi Network |
| `DELETE` | `/wifiNetworks/{wifiNetworkId}/radiusServerProfiles/{radiusId}` | Deactivate RADIUS Server Profile On Wi-Fi Network |
| `PUT` | `/wifiNetworks/{wifiNetworkId}/radiusServerProfiles/{radiusId}` | Activate RADIUS Server Profile On Wi-Fi Network |

### RADIUS Server Profile Template

| Method | Path | Summary |
|--------|------|----------|
| `POST` | `/templates/radiusServerProfiles` | Add RADIUS Server Profile Template |
| `DELETE` | `/templates/radiusServerProfiles/{radiusServerProfileTemplateId}` | Delete RADIUS Server Profile Template |
| `GET` | `/templates/radiusServerProfiles/{radiusServerProfileTemplateId}` | Get RADIUS Server Profile Template |
| `PUT` | `/templates/radiusServerProfiles/{radiusServerProfileTemplateId}` | Update RADIUS Server Profile Template |
| `DELETE` | `/templates/venues/{venueTemplateId}/radiusServerProfiles/{radiusServerProfileTemplateId}` | Deactivate RADIUS Server Profile On Venue |
| `PUT` | `/templates/venues/{venueTemplateId}/radiusServerProfiles/{radiusServerProfileTemplateId}` | Activate RADIUS Server Profile Template On Venue Template |
| `GET` | `/templates/wifiNetworks/{wifiNetworkTemplateId}/radiusServerProfileSettings` | Get RADIUS Server Profile Template Settings On Wi-Fi Network Template |
| `PUT` | `/templates/wifiNetworks/{wifiNetworkTemplateId}/radiusServerProfileSettings` | Update RADIUS Server Profile Template Settings On Wi-Fi Network Template |
| `DELETE` | `/templates/wifiNetworks/{wifiNetworkTemplateId}/radiusServerProfiles/{radiusServerProfileTemplateId}` | Deactivate RADIUS Server Profile Template On Wi-Fi Network Template |
| `PUT` | `/templates/wifiNetworks/{wifiNetworkTemplateId}/radiusServerProfiles/{radiusServerProfileTemplateId}` | Activate RADIUS Server Profile Template On Wi-Fi Network Template |

### Rogue AP Detection Policy

| Method | Path | Summary |
|--------|------|----------|
| `POST` | `/roguePolicies` | Create Rogue AP Detection Policy |
| `DELETE` | `/roguePolicies/{roguePolicyId}` | Delete Rogue AP Detection Policy |
| `GET` | `/roguePolicies/{roguePolicyId}` | Get Rogue AP Detection Policy |
| `PUT` | `/roguePolicies/{roguePolicyId}` | Update Rogue AP Detection Policy |
| `DELETE` | `/venues/{venueId}/roguePolicies/{roguePolicyId}` | Deactivate Rogue AP Detection Policy On Venue |
| `PUT` | `/venues/{venueId}/roguePolicies/{roguePolicyId}` | Activate Rogue AP Detection Policy On Venue |
| `GET` | `/venues/{venueId}/roguePolicySettings` | Get Venue Rogue Policy Settings |
| `PUT` | `/venues/{venueId}/roguePolicySettings` | Update Venue Rogue Policy Settings |

### Rogue AP Detection Policy Template

| Method | Path | Summary |
|--------|------|----------|
| `POST` | `/templates/roguePolicies` | Create Rogue AP Detection Policy Template |
| `DELETE` | `/templates/roguePolicies/{roguePolicyTemplateId}` | Delete Rogue AP Detection Policy Template |
| `GET` | `/templates/roguePolicies/{roguePolicyTemplateId}` | Get Rogue AP Detection Policy Template |
| `PUT` | `/templates/roguePolicies/{roguePolicyTemplateId}` | Update Rogue AP Detection Policy Template |
| `DELETE` | `/templates/venues/{venueTemplateId}/roguePolicies/{roguePolicyTemplateId}` | Deactivate Rogue AP Detection Policy On Venue Template |
| `PUT` | `/templates/venues/{venueTemplateId}/roguePolicies/{roguePolicyTemplateId}` | Activate Rogue AP Detection Policy On Venue Template |
| `GET` | `/templates/venues/{venueTemplateId}/roguePolicySettings` | Get Venue Template Rogue Policy Settings |
| `PUT` | `/templates/venues/{venueTemplateId}/roguePolicySettings` | Update Venue Template Rogue Policy Settings |

### SAML Identity Provider Profile

| Method | Path | Summary |
|--------|------|----------|
| `PUT` | `/wifiNetworks/{wifiNetworkId}/samlIdpProfiles/{samlIdpProfileId}` | Activate SAML Identity Provider Profile On Wi-Fi Network |

### SNMP Agent Profile

| Method | Path | Summary |
|--------|------|----------|
| `POST` | `/snmpAgentProfiles` | Create SNMP Agent Profile |
| `DELETE` | `/snmpAgentProfiles/{snmpAgentProfileId}` | Delete SNMP Agent Profile |
| `GET` | `/snmpAgentProfiles/{snmpAgentProfileId}` | Get SNMP Agent Profile |
| `PUT` | `/snmpAgentProfiles/{snmpAgentProfileId}` | Update SNMP Agent Profile |
| `GET` | `/venues/{venueId}/aps/{serialNumber}/snmpAgentProfileSettings` | Get SNMP Agent Profile Settings On AP |
| `PUT` | `/venues/{venueId}/aps/{serialNumber}/snmpAgentProfileSettings` | Update SNMP Agent Profile Settings On AP |
| `DELETE` | `/venues/{venueId}/aps/{serialNumber}/snmpAgentProfiles/{snmpAgentProfileId}` | Deactivate SNMP Agent Profile On AP |
| `PUT` | `/venues/{venueId}/aps/{serialNumber}/snmpAgentProfiles/{snmpAgentProfileId}` | Activate SNMP Agent Profile On AP |
| `DELETE` | `/venues/{venueId}/snmpAgentProfiles/{snmpAgentProfileId}` | Deactivate SNMP Agent Profile On Venue |
| `PUT` | `/venues/{venueId}/snmpAgentProfiles/{snmpAgentProfileId}` | Activate SNMP Agent Profile On Venue |

### SoftGRE Profile

| Method | Path | Summary |
|--------|------|----------|
| `POST` | `/softGreProfiles` | Add SoftGRE Profile |
| `DELETE` | `/softGreProfiles/{softGreProfileId}` | Delete SoftGRE Profile |
| `GET` | `/softGreProfiles/{softGreProfileId}` | Get SoftGRE Profile |
| `PUT` | `/softGreProfiles/{softGreProfileId}` | Update SoftGRE Profile |
| `DELETE` | `/venues/{venueId}/apModels/{apModel}/lanPorts/{portId}/softGreProfiles/{softGreProfileId}` | Deactivate SoftGRE Profile On Venue AP Model LAN Port |
| `PUT` | `/venues/{venueId}/apModels/{apModel}/lanPorts/{portId}/softGreProfiles/{softGreProfileId}` | Activate SoftGRE Profile On Venue AP Model LAN Port |
| `DELETE` | `/venues/{venueId}/aps/{serialNumber}/lanPorts/{portId}/softGreProfiles/{softGreProfileId}` | Deactivate SoftGRE Profile On Venue AP LAN Port |
| `PUT` | `/venues/{venueId}/aps/{serialNumber}/lanPorts/{portId}/softGreProfiles/{softGreProfileId}` | Activate SoftGRE Profile On Venue AP LAN Port |
| `DELETE` | `/venues/{venueId}/wifiNetworks/{wifiNetworkId}/softGreProfiles/{softGreProfileId}` | Deactivate SoftGRE Profile On Venue Wi-Fi Network |
| `PUT` | `/venues/{venueId}/wifiNetworks/{wifiNetworkId}/softGreProfiles/{softGreProfileId}` | Activate SoftGRE Profile On Venue Wi-Fi Network |

### Syslog Server Profile

| Method | Path | Summary |
|--------|------|----------|
| `POST` | `/syslogServerProfiles` | Create Syslog Server Profile |
| `DELETE` | `/syslogServerProfiles/{syslogServerProfileId}` | Delete Syslog Server Profile |
| `GET` | `/syslogServerProfiles/{syslogServerProfileId}` | Get Syslog Server Profile |
| `PUT` | `/syslogServerProfiles/{syslogServerProfileId}` | Update Syslog Server Profile |
| `DELETE` | `/venues/{venueId}/syslogServerProfiles/{syslogServerProfileId}` | Deactivate Syslog Server Profile On Venue |
| `PUT` | `/venues/{venueId}/syslogServerProfiles/{syslogServerProfileId}` | Activate Syslog Server Profile On Venue |

### Syslog Server Profile Template

| Method | Path | Summary |
|--------|------|----------|
| `POST` | `/templates/syslogServerProfiles` | Create Syslog Server Profile Template |
| `DELETE` | `/templates/syslogServerProfiles/{syslogServerProfileTemplateId}` | Delete Syslog Server Profile Template |
| `GET` | `/templates/syslogServerProfiles/{syslogServerProfileTemplateId}` | Get Syslog Server Profile Template |
| `PUT` | `/templates/syslogServerProfiles/{syslogServerProfileTemplateId}` | Update Syslog Server Profile Template |
| `DELETE` | `/templates/venues/{venueTemplateId}/syslogServerProfiles/{syslogServerProfileTemplateId}` | Deactivate Syslog Server Profile Template On Venue Template |
| `PUT` | `/templates/venues/{venueTemplateId}/syslogServerProfiles/{syslogServerProfileTemplateId}` | Activate Syslog Server Profile Template On Venue Template |

### Tunnel Service Profile

| Method | Path | Summary |
|--------|------|----------|
| `POST` | `/tunnelServiceProfiles` | Add Tunnel Service Profile |
| `DELETE` | `/tunnelServiceProfiles/{tunnelServiceProfileId}` | Delete Tunnel Service Profile |
| `GET` | `/tunnelServiceProfiles/{tunnelServiceProfileId}` | Get Tunnel Service Profile |
| `PATCH` | `/tunnelServiceProfiles/{tunnelServiceProfileId}` | Partial Update Tunnel Service Profile |
| `PUT` | `/tunnelServiceProfiles/{tunnelServiceProfileId}` | Update Tunnel Service Profile |

### Tunnel Service Profile Template

| Method | Path | Summary |
|--------|------|----------|
| `POST` | `/templates/tunnelServiceProfiles` | Add Tunnel Service Profile Template |
| `DELETE` | `/templates/tunnelServiceProfiles/{tunnelServiceProfileTemplateId}` | Delete Tunnel Service Profile Template |
| `GET` | `/templates/tunnelServiceProfiles/{tunnelServiceProfileTemplateId}` | Get Tunnel Service Profile Template |
| `PUT` | `/templates/tunnelServiceProfiles/{tunnelServiceProfileTemplateId}` | Update Tunnel Service Profile Template |

### VLAN Pool Profile

| Method | Path | Summary |
|--------|------|----------|
| `DELETE` | `/venues/{venueId}/wifiNetworks/{wifiNetworkId}/apGroups/{apGroupId}/vlanPoolProfiles/{vlanPoolProfileId}` | Deactivate VLAN Pool Profile On AP Group |
| `PUT` | `/venues/{venueId}/wifiNetworks/{wifiNetworkId}/apGroups/{apGroupId}/vlanPoolProfiles/{vlanPoolProfileId}` | Activate VLAN Pool Profile On AP Group |
| `POST` | `/vlanPoolProfiles` | Add VLAN Pool Profile |
| `DELETE` | `/vlanPoolProfiles/{vlanPoolProfileId}` | Delete VLAN Pool Profile |
| `GET` | `/vlanPoolProfiles/{vlanPoolProfileId}` | Get VLAN Pool |
| `PUT` | `/vlanPoolProfiles/{vlanPoolProfileId}` | Update VLAN Pool Profile |
| `DELETE` | `/wifiNetworks/{wifiNetworkId}/vlanPoolProfiles/{vlanPoolProfileId}` | Deactivate VLAN Pool Profile On Wi-Fi Network |
| `PUT` | `/wifiNetworks/{wifiNetworkId}/vlanPoolProfiles/{vlanPoolProfileId}` | Activate VLAN Pool Profile On Wi-Fi Network |

### VLAN Pool Profile Template

| Method | Path | Summary |
|--------|------|----------|
| `DELETE` | `/templates/venues/{venueTemplateId}/wifiNetworks/{wifiNetworkTemplateId}/apGroups/{apGroupTemplateId}/vlanPoolProfiles/{vlanPoolProfileTemplateId}` | Deactivate VLAN Pool Profile Template On AP Group Template |
| `PUT` | `/templates/venues/{venueTemplateId}/wifiNetworks/{wifiNetworkTemplateId}/apGroups/{apGroupTemplateId}/vlanPoolProfiles/{vlanPoolProfileTemplateId}` | Activate VLAN Pool Profile Template On AP Group Template |
| `POST` | `/templates/vlanPoolProfiles` | Add VLAN Pool Profile Template |
| `DELETE` | `/templates/vlanPoolProfiles/{vlanPoolProfileTemplateId}` | Delete VLAN Pool Profile Template |
| `GET` | `/templates/vlanPoolProfiles/{vlanPoolProfileTemplateId}` | Get VLAN Pool Profile Template |
| `PUT` | `/templates/vlanPoolProfiles/{vlanPoolProfileTemplateId}` | Update VLAN Pool Profile Template |
| `DELETE` | `/templates/wifiNetworks/{wifiNetworkTemplateId}/vlanPoolProfiles/{vlanPoolProfileTemplateId}` | Deactivate VLAN Pool Profile Template On Wi-Fi Network Template |
| `PUT` | `/templates/wifiNetworks/{wifiNetworkTemplateId}/vlanPoolProfiles/{vlanPoolProfileTemplateId}` | Activate VLAN Pool Profile Template On Wi-Fi Network Template |

### VRIoT Management

| Method | Path | Summary |
|--------|------|----------|
| `DELETE` | `/venues/{venueId}/aps/{serialNumber}/iotControllers/{iotControllerId}` | Disassociate IoT Controller from AP |
| `PUT` | `/venues/{venueId}/aps/{serialNumber}/iotControllers/{iotControllerId}` | Associate IoT Controller with AP |
| `DELETE` | `/venues/{venueId}/iotControllers/{iotControllerId}` | Disassociate IoT Controller from Venue |
| `PUT` | `/venues/{venueId}/iotControllers/{iotControllerId}` | Associate IoT Controller with Venue |

### Wi-Fi Calling Service Profile

| Method | Path | Summary |
|--------|------|----------|
| `POST` | `/wifiCallingServiceProfiles` | Create Wi-Fi Calling Service Profile |
| `DELETE` | `/wifiCallingServiceProfiles/{wifiCallingServiceProfileId}` | Delete Wi-Fi Calling Service Profile |
| `GET` | `/wifiCallingServiceProfiles/{wifiCallingServiceProfileId}` | Get Wi-Fi Calling Service Profile |
| `PUT` | `/wifiCallingServiceProfiles/{wifiCallingServiceProfileId}` | Update Wi-Fi Calling Service Profile |
| `DELETE` | `/wifiNetworks/{wifiNetworkId}/wifiCallingServiceProfiles/{wifiCallingServiceProfileId}` | Deactivate Wi-Fi Calling Service Profile On Wi-Fi Network |
| `PUT` | `/wifiNetworks/{wifiNetworkId}/wifiCallingServiceProfiles/{wifiCallingServiceProfileId}` | Activate Wi-Fi Calling Service Profile On Wi-Fi Network |

### Wi-Fi Calling Service Profile Template

| Method | Path | Summary |
|--------|------|----------|
| `POST` | `/templates/wifiCallingServiceProfiles` | Create Wi-Fi Calling Service Profile Template |
| `DELETE` | `/templates/wifiCallingServiceProfiles/{wifiCallingServiceProfileId}` | Delete Wi-Fi Calling Service Profile Template |
| `GET` | `/templates/wifiCallingServiceProfiles/{wifiCallingServiceProfileId}` | Get Wi-Fi Calling Service Profile Template |
| `PUT` | `/templates/wifiCallingServiceProfiles/{wifiCallingServiceProfileId}` | Update Wi-Fi Calling Service Profile Template |
| `POST` | `/templates/wifiCallingServiceProfiles/{wifiCallingServiceProfileId}/cloneSettings` | Clone Wi-Fi Calling Service Profile Template |
| `DELETE` | `/templates/wifiNetworks/{wifiNetworkTemplateId}/wifiCallingServiceProfiles/{wifiCallingServiceProfileId}` | Deactivate Wi-Fi Calling Service Profile Template On Wi-Fi Network Template |
| `PUT` | `/templates/wifiNetworks/{wifiNetworkTemplateId}/wifiCallingServiceProfiles/{wifiCallingServiceProfileId}` | Activate Wi-Fi Calling Service Profile Template On Wi-Fi Network Template |

### Wi-Fi Network

| Method | Path | Summary |
|--------|------|----------|
| `DELETE` | `/venues/{venueId}/wifiNetworks/{wifiNetworkId}` | Deactivate Wi-Fi Network On Venue |
| `PUT` | `/venues/{venueId}/wifiNetworks/{wifiNetworkId}` | Activate Wi-Fi Network On Venue |
| `GET` | `/venues/{venueId}/wifiNetworks/{wifiNetworkId}/settings` | Get Venue Wi-Fi Network Settings |
| `PUT` | `/venues/{venueId}/wifiNetworks/{wifiNetworkId}/settings` | Update Venue Wi-Fi Network Settings |
| `POST` | `/wifiNetworks` | Create Wi-Fi Network |
| `GET` | `/wifiNetworks/hotspot20IdentityProviders` | Get Predefined Hotspot 2.0 Identity Providers |
| `GET` | `/wifiNetworks/hotspot20Operators` | Get Predefined Hotspot 2.0 Operators |
| `GET` | `/wifiNetworks/qosMapSetOptions` | Get Default Options for QoS Map Set |
| `GET` | `/wifiNetworks/recoveryPassphraseSettings` | Get Wi-Fi Recovery Network Passphrase Settings |
| `PUT` | `/wifiNetworks/recoveryPassphraseSettings` | Update Wi-Fi Recovery Network Passphrase Settings |
| `GET` | `/wifiNetworks/wisprProviders` | Get External WISPr Providers |
| `DELETE` | `/wifiNetworks/{wifiNetworkId}` | Delete Wi-Fi Network |
| `GET` | `/wifiNetworks/{wifiNetworkId}` | Get Wi-Fi Network |
| `PUT` | `/wifiNetworks/{wifiNetworkId}` | Update Wi-Fi Network |

### Wi-Fi Network Template

| Method | Path | Summary |
|--------|------|----------|
| `DELETE` | `/templates/venues/{venueId}/wifiNetworks/{wifiNetworkId}` | Deactivate Wi-Fi Network Template On Venue Template |
| `PUT` | `/templates/venues/{venueId}/wifiNetworks/{wifiNetworkId}` | Activate Wi-Fi Network Template On Venue Template |
| `GET` | `/templates/venues/{venueId}/wifiNetworks/{wifiNetworkId}/settings` | Get Venue Wi-Fi Network Template Settings |
| `PUT` | `/templates/venues/{venueId}/wifiNetworks/{wifiNetworkId}/settings` | Update Venue Wi-Fi Network Template Settings |
| `POST` | `/templates/wifiNetworks` | Create Wi-Fi Network Template |
| `DELETE` | `/templates/wifiNetworks/{wifiNetworkTemplateId}` | Delete Wi-Fi Network Template |
| `GET` | `/templates/wifiNetworks/{wifiNetworkTemplateId}` | Get Wi-Fi Network Template |
| `PUT` | `/templates/wifiNetworks/{wifiNetworkTemplateId}` | Update Wi-Fi Network Template |
| `POST` | `/templates/wifiNetworks/{wifiNetworkTemplateId}/cloneSettings` | Clone Wi-Fi Network Template |

### Wi-Fi Portal Service Profile

| Method | Path | Summary |
|--------|------|----------|
| `PUT` | `/wifiNetworks/{wifiNetworkId}/portalServiceProfiles/{portalServiceProfileId}` | Activate Portal Service Profile On Wi-Fi Network |

---

## Workflow Actions API (v0.0.2)

**Base URL:** `http://localhost:8080`

**Rate Limit:** # RateLimit

**Endpoint Count:** 10

### Enrollment Action API

| Method | Path | Summary |
|--------|------|----------|
| `GET` | `/enrollmentActions` | Get All Enrollment Actions Across Action Types |
| `POST` | `/enrollmentActions` | Create Enrollment Action Across Action Types |
| `POST` | `/enrollmentActions/query` | Query Enrollment Actions Across Action Types |
| `DELETE` | `/enrollmentActions/{actionId}` | Delete Specific Enrollment Actions |
| `GET` | `/enrollmentActions/{actionId}` | Get Enrollment Action Configuration for Specific Action Identifier |
| `PATCH` | `/enrollmentActions/{actionId}` | Selectively Updates Enrollment Actions |

### Enrollment Action Files API

| Method | Path | Summary |
|--------|------|----------|
| `POST` | `/enrollmentActions/files` | Upload File |
| `DELETE` | `/enrollmentActions/files/{fileId}` | Delete File |
| `GET` | `/enrollmentActions/files/{fileId}` | Get Signed URL for Download |

### Enrollment Action Type API

| Method | Path | Summary |
|--------|------|----------|
| `GET` | `/enrollmentActions/actionTypes/{actionType}` | Get All Enrollment Action Configuration for Specific Action Type |

---

## Workflow Management API (v0.0.3)

**Base URL:** `http://localhost:8080`

**Rate Limit:** The Workflow Management REST API's allow the creation of a workflow, and steps within that workflow. Steps, and split step options must be assigned actions that are already defined through the /enrollmentActions api.  Please see that API for additional information. For split steps only the action definition is to be provided, but the options require a matching action type, and must be of type split.## Rate Limit

**Endpoint Count:** 27

### Action Definition

| Method | Path | Summary |
|--------|------|----------|
| `GET` | `/workflowActionDefinitions` | Get All Action Definitions |
| `POST` | `/workflowActionDefinitions/query` | Query Action Definitions |
| `GET` | `/workflowActionDefinitions/{definitionId}` | Get Action Definition |
| `GET` | `/workflowActionDefinitions/{definitionId}/requiredPriorDefinitions` | Get Prior Required Actions |

### Enrollment UI Configuration

| Method | Path | Summary |
|--------|------|----------|
| `DELETE` | `/workflows/{workflowId}/uiConfigurations` | Delete UI Configuration |
| `GET` | `/workflows/{workflowId}/uiConfigurations` | Get Workflow's UI Configuration |
| `POST` | `/workflows/{workflowId}/uiConfigurations` | Update Workflows UI Configuration |
| `GET` | `/workflows/{workflowId}/uiConfigurations/{imageType}` | Get UI Configuration's Image |

### Split Options

| Method | Path | Summary |
|--------|------|----------|
| `GET` | `/workflows/{workflowId}/steps/{stepId}/splitOptions` | Get All Split Options |
| `POST` | `/workflows/{workflowId}/steps/{stepId}/splitOptions` | Creates Split Option |
| `DELETE` | `/workflows/{workflowId}/steps/{stepId}/splitOptions/{optionId}` | Delete Split Option |
| `GET` | `/workflows/{workflowId}/steps/{stepId}/splitOptions/{optionId}` | Get Split Option |
| `POST` | `/workflows/{workflowId}/steps/{stepId}/splitOptions/{optionId}/nextSteps` | Creates Step Under Option |

### Steps

| Method | Path | Summary |
|--------|------|----------|
| `GET` | `/workflows/{workflowId}/steps` | Get All Step |
| `POST` | `/workflows/{workflowId}/steps/query` | Query All Step |
| `DELETE` | `/workflows/{workflowId}/steps/{stepId}` | Delete Step |
| `GET` | `/workflows/{workflowId}/steps/{stepId}` | Get Step |
| `DELETE` | `/workflows/{workflowId}/steps/{stepId}/descendantSteps` | Delete Descendant Step |
| `POST` | `/workflows/{workflowId}/steps/{stepId}/nextSteps` | Create Step |

### Workflow

| Method | Path | Summary |
|--------|------|----------|
| `POST` | `/workflows` | Create Workflow |
| `POST` | `/workflows/query` | Get All Current Workflow |
| `DELETE` | `/workflows/{workflowId}` | Delete Workflow |
| `GET` | `/workflows/{workflowId}` | Get Workflow |
| `PATCH` | `/workflows/{workflowId}` | Update a Workflow |
| `POST` | `/workflows/{workflowId}/steps/{stepId}/nextSteps/workflows/{referencedWorkflowId}` | Import a Workflow Into the Existing Workflow |
| `POST` | `/workflows/{workflowId}/versions/query` | Get All Workflows Including Versioned |
| `POST` | `/workflows/{workflowId}/workflows` | Create a New Workflow from the Specified Workflow |

---

