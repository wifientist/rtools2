import requests
import time

class R1_calls:

    def __init__(self, region=None):
        if region == 'EU': self.host = 'api.eu.ruckus.cloud'
        elif region == 'ASIA': self.host = 'api.asia.ruckus.cloud'
        else: self.host = 'api.ruckus.cloud'
        pass

    def getJWTraw(self, username, password, verify=False):
        url = f"https://{self.host}/token"
        body = {'username':username,'password':password}
        r = requests.post(url,json=body,verify=False)
        print('getJWT -- ',r)
        return r

    def getJWT(self, username, password):
        url = f"https://{self.host}/token"
        body = {'username':username,'password':password}
        r = requests.post(url,json=body,verify=False)
        print('getJWT -- ',r)
        return r.json()

    #get all MSP labels that an authenticated JWT can access
    def getmspLabels(self, token):
        url = f"https://{self.host}/mspLabels"
        body={}
        headers={'Content-Type': 'application/json','Authorization':f'Bearer {token}'}
        r = requests.get(url, headers=headers)
        print('getmspLabels -- ',r)
        return r.json()

    #get all ECs
    def getmspECs(self, token):
        url = f"https://{self.host}/mspecs/query"
        body={'fields':['check-all','id','name','tenantType','mspAdminCount','mspEcAdminCount'],'sortField':'name','sortOrder':'ASC','filters':{'tenantType':['MSP_EC']}}
        headers={'Content-Type': 'application/json','Authorization':f'Bearer {token}'}
        r = requests.post(url, json=body, headers=headers)
        print('getmspECs -- ',r)
        return r.json()
        #{"fields":["check-all","id","name","tenantType","mspAdminCount","mspEcAdminCount","wifiLicense","switchLicens"],"page":1,"pageSize":10,"defaultPageSize":10,"total":0,"sortField":"name","sortOrder":"ASC","filters":{"tenantType":["MSP_INTEGRATOR","MSP_INSTALLER"]}}

    #get all tech partners
    def getmspTechPartners(self, token):
        url = f"https://{self.host}/mspecs/query"
        body={'fields':['check-all','id','name','tenantType','mspAdminCount','mspEcAdminCount'],'sortField':'name','sortOrder':'ASC','filters':{'tenantType':['MSP_INSTALLER','MSP_INTEGRATOR']}}
        headers={'Content-Type': 'application/json','Authorization':f'Bearer {token}'}
        r = requests.post(url, json=body, headers=headers)
        print('getmspECs -- ',r)
        return r.json()
        #{"fields":["check-all","id","name","tenantType","mspAdminCount","mspEcAdminCount","wifiLicense","switchLicens"],"page":1,"pageSize":10,"defaultPageSize":10,"total":0,"sortField":"name","sortOrder":"ASC","filters":{"tenantType":["MSP_INTEGRATOR","MSP_INSTALLER"]}}

    #get all MSP admins that an authenticated JWT can access
    def getAdmins(self, token):
        url = f"https://{self.host}/admins"
        body={}
        headers={'Content-Type': 'application/json','Authorization': f'Bearer {token}'}
        r = requests.get(url, headers=headers)
        print('getAdmins -- ',r)
        return r.json()

    #get all MSP admins that an authenticated JWT can access
    def getmspAdmins(self, token, id):
        url = f"https://{self.host}/mspCustomers/{id}/admins"
        body={}
        headers={'Content-Type': 'application/json','Authorization': f'Bearer {token}'}
        r = requests.get(url, headers=headers)
        print('getmspAdmins -- ',r)
        return r.json()

    #get Delegations
    def getDelegations(self, token, payload=None):
        url = f"https://{self.host}/tenants/delegations"
        body={}
        headers={'Content-Type': 'application/json','Authorization': f'Bearer {token}'}
        #if payload: r = requests.get(url, json=payload, headers=headers)
        #else: r = requests.get(url, headers=headers)
        r = requests.get(url, headers=headers)
        print('getDelegations -- ',r)
        return r.json()

    #get all MSP Delegations by customer id
    def getmspDelegations(self, token, id):
        url = f"https://{self.host}/mspCustomers/{id}/mspadmins"
        body={}
        headers={'Content-Type': 'application/json','Authorization': f'Bearer {token}'}
        r = requests.get(url, headers=headers)
        print('getmspDelegations -- ',r)
        return r.json()

    #get all tenant ID that an authenticated JWT can access
    def getmspCustomers(self, token):
        url = f"https://{self.host}/mspCustomers"
        body={}
        headers={'Content-Type': 'application/json','Authorization': f'Bearer {token}'}
        r = requests.get(url, headers=headers)
        print('getmspCustomers -- ',r)
        return r.json()

    #get specific tenant ID that an authenticated JWT can access
    def getmspCustomer(self, token, id):
        url = f"https://{self.host}/mspCustomers/{id}"
        body={}
        headers={'Content-Type': 'application/json','Authorization': f'Bearer {token}'}
        r = requests.get(url, headers=headers)
        print('getmspCustomer/id -- ',r)
        return r.json()

    #get entitlements
    def getEntitlements(self, token):
        url = f"https://{self.host}/entitlements"
        body={}
        headers={'Content-Type': 'application/json','Authorization': f'Bearer {token}'}
        r = requests.get(url, headers=headers)
        print('getEntitlements -- ',r)
        return r.json()

    #get entitlement summaries
    def getEntitlementSummaries(self, token):
        url = f"https://{self.host}/entitlements/summaries"
        body={}
        headers={'Content-Type': 'application/json','Authorization': f'Bearer {token}'}
        r = requests.get(url, headers=headers)
        print('getEntitlementSummaries -- ',r)
        return r.json()

    #get MSP entitlements
    def getmspEntitlements(self, token):
        url = f"https://{self.host}/mspEntitlements"
        body={}
        headers={'Content-Type': 'application/json','Authorization': f'Bearer {token}'}
        r = requests.get(url, headers=headers)
        print('getmspEntitlements -- ',r)
        return r.json()

    #get MSP assignments
    def getmspAssignments(self, token):
        url = f"https://{self.host}/assignments/summaries"
        body={}
        headers={'Content-Type': 'application/json','Authorization': f'Bearer {token}'}
        r = requests.get(url, headers=headers)
        print('getmspAssignments -- ',r)
        return r.json()

    #get venues
    def getVenues(self, token, id=None):
        url = f"https://{self.host}/venues"
        body={}
        headers={'Content-Type': 'application/json','Authorization': f'Bearer {token}'}
        if id: headers['x-rks-tenantid'] = id
        r = requests.get(url, headers=headers)
        print('getVenues -- ',r)
        return r.json()
        
    #get venue wifi settings
    def getVenueWifi(self, token, id=None):
        url = f"https://{self.host}/venues/wifiSettings"
        body={}
        headers={'Content-Type': 'application/json','Authorization': f'Bearer {token}'}
        if id: headers['x-rks-tenantid'] = id
        r = requests.get(url, headers=headers)
        print('getVenueWifiSettings -- ',r)
        return r.json()
     
    #get venue switch settings
    def getVenueSwitchSettings(self, token, tenantId=None, venueId=None):
        url = f"https://{self.host}/venues/{venueId}/switchSettings"
        body={}
        headers={'Content-Type': 'application/json','Authorization': f'Bearer {token}'}
        if tenantId: headers['x-rks-tenantid'] = tenantId
        r = requests.get(url, headers=headers)
        print('getVenueSwitchSettings -- ',r)
        return r.json()
    
    #get all networks
    def getNetworks(self, token, id=None):
        url = f"https://{self.host}/networks"
        body={}
        headers={'Content-Type': 'application/json','Authorization': f'Bearer {token}'}
        if id: headers['x-rks-tenantid'] = id
        r = requests.get(url, headers=headers)
        print('getNetworks -- ',r)
        return r.json()    

    #get all networks
    def getNetwork(self, token, id=None, net_id=None):
        url = f"https://{self.host}/networks"
        if net_id: url += f'/{net_id}'
        body={}
        headers={'Content-Type': 'application/json','Authorization': f'Bearer {token}'}
        if id: headers['x-rks-tenantid'] = id
        r = requests.get(url, headers=headers)
        print('getNetwork -- ',r)
        return r.json()         

    #get AP Groups
    #required components: 
    def getAPgroups(self, token, tenantid=None):
        url = f"https://{self.host}/venues/apGroups"
        headers={'Content-Type': 'application/json','Authorization': f'Bearer {token}'}
        if tenantid: headers['x-rks-tenantid'] = tenantid
        r = requests.get(url, headers=headers)
        print('getAPgroups -- ',r)
        return r.json()
    
    #get network activations by venue
    #required components: venueid
    def getActivations(self, token, payload, tenantid=None):
        url = f"https://{self.host}/networkActivations/query"
        headers={'Content-Type': 'application/json','Authorization': f'Bearer {token}'}
        if tenantid: headers['x-rks-tenantid'] = tenantid
        r = requests.post(url, json=payload, headers=headers)
        print('getActivations -- ',r)
        return r.json()

    #get Access Control Profiles
    def getAccessControlProfiles(self, token, tenantid=None):
        url = f"https://{self.host}/accessControlProfiles"
        headers={'Content-Type': 'application/json','Authorization': f'Bearer {token}'}
        if tenantid: headers['x-rks-tenantid'] = tenantid
        r = requests.get(url, headers=headers)
        print('getAccessControlProfiles-- ',r)
        return r.json()

    #get Application Policies
    def getApplicationPolicies(self, token, tenantid=None):
        url = f"https://{self.host}/applicationPolicies"
        headers={'Content-Type': 'application/json','Authorization': f'Bearer {token}'}
        if tenantid: headers['x-rks-tenantid'] = tenantid
        r = requests.get(url, headers=headers)
        print('getApplicationPolicies-- ',r)
        return r.json()

    #get enhanced app policies
    def getenhancedApplicationPolicies(self, token, payload, tenantid=None):
        url = f"https://{self.host}/enhancedApplicationPolicies/query"
        headers={'Content-Type': 'application/json','Authorization': f'Bearer {token}'}
        if tenantid: headers['x-rks-tenantid'] = tenantid
        r = requests.post(url, json=payload, headers=headers)
        print('getenhancedAppPolicies -- ',r)
        return r.json()

    #get enhanced access control policies
    def getenhancedAccessControlProfiles(self, token, payload, tenantid=None):
        url = f"https://{self.host}/enhancedAccessControlProfiles/query"
        headers={'Content-Type': 'application/json','Authorization': f'Bearer {token}'}
        if tenantid: headers['x-rks-tenantid'] = tenantid
        r = requests.post(url, json=payload, headers=headers)
        print('getenhancedAccessControlProfiles -- ',r)
        return r.json()

    #get L2 ACL policies
    def getL2AclPolicies(self, token, tenantid=None):
        url = f"https://{self.host}/l2AclPolicies"
        headers={'Content-Type': 'application/json','Authorization': f'Bearer {token}'}
        if tenantid: headers['x-rks-tenantid'] = tenantid
        r = requests.get(url, headers=headers)
        print('getL2AclPolicies-- ',r)
        return r.json()

    #get L2 ACL policy
    def getL2AclPolicy(self, token, id, tenantid=None):
        url = f"https://{self.host}/l2AclPolicies/{id}"
        headers={'Content-Type': 'application/json','Authorization': f'Bearer {token}'}
        if tenantid: headers['x-rks-tenantid'] = tenantid
        r = requests.get(url, headers=headers)
        print('getL2AclPolicy-- ',r)
        return r.json()

    #get L3 ACL policies
    def getL3AclPolicies(self, token, tenantid=None):
        url = f"https://{self.host}/l3AclPolicies"
        headers={'Content-Type': 'application/json','Authorization': f'Bearer {token}'}
        if tenantid: headers['x-rks-tenantid'] = tenantid
        r = requests.get(url, headers=headers)
        print('getL3AclPolicies-- ',r)
        return r.json()

    #get L3 ACL policy
    def getL3AclPolicy(self, token, id, tenantid=None):
        url = f"https://{self.host}/l3AclPolicies/{id}"
        headers={'Content-Type': 'application/json','Authorization': f'Bearer {token}'}
        if tenantid: headers['x-rks-tenantid'] = tenantid
        r = requests.get(url, headers=headers)
        print('getL3AclPolicy-- ',r)
        return r.json()

    #get enhanced L2 policies
    def getenhancedL2AclPolicies(self, token, payload, tenantid=None):
        url = f"https://{self.host}/enhancedL2AclPolicies/query"
        headers={'Content-Type': 'application/json','Authorization': f'Bearer {token}'}
        if tenantid: headers['x-rks-tenantid'] = tenantid
        r = requests.post(url, json=payload, headers=headers)
        print('getenhancedL2AclPolicies -- ',r)
        return r.json()

    #get enhanced L3 policies
    def getenhancedL3AclPolicies(self, token, payload, tenantid=None):
        url = f"https://{self.host}/enhancedL3AclPolicies/query"
        headers={'Content-Type': 'application/json','Authorization': f'Bearer {token}'}
        if tenantid: headers['x-rks-tenantid'] = tenantid
        r = requests.post(url, json=payload, headers=headers)
        print('getenhancedL3AclPolicies -- ',r)
        return r.json()

    #get device policies
    def getDevicePolicies(self, token, tenantid=None):
        url = f"https://{self.host}/devicePolicies"
        headers={'Content-Type': 'application/json','Authorization': f'Bearer {token}'}
        if tenantid: headers['x-rks-tenantid'] = tenantid
        r = requests.get(url, headers=headers)
        print('getDevicePolicies-- ',r)
        return r.json()

    #get enhanced device policies
    def getenhancedDevicePolicies(self, token, payload, tenantid=None):
        url = f"https://{self.host}/enhancedDevicePolicies/query"
        headers={'Content-Type': 'application/json','Authorization': f'Bearer {token}'}
        if tenantid: headers['x-rks-tenantid'] = tenantid
        r = requests.post(url, json=payload, headers=headers)
        print('getenhancedDevicePolicies -- ',r)
        return r.json()

    #get Client Isolation Allowlists
    def getClientIsolationAllowlists(self, token, tenantid=None):
        url = f"https://{self.host}/isolationAllowlists"
        headers={'Content-Type': 'application/json','Authorization': f'Bearer {token}'}
        if tenantid: headers['x-rks-tenantid'] = tenantid
        r = requests.get(url, headers=headers)
        print('getClientIsolationAllowlists-- ',r)
        return r.json()

    #get Rogue AP Policy Profiles
    def getRogueApPolicyProfiles(self, token, tenantid=None):
        url = f"https://{self.host}/rogueApPolicyProfiles"
        headers={'Content-Type': 'application/json','Authorization': f'Bearer {token}'}
        if tenantid: headers['x-rks-tenantid'] = tenantid
        r = requests.get(url, headers=headers)
        print('getRogueApPolicyProfiles-- ',r)
        return r.json()
        
    #get Syslog
    def getSyslogServerProfiles(self, token, tenantid=None):
        url = f"https://{self.host}/syslogServerProfiles"
        headers={'Content-Type': 'application/json','Authorization': f'Bearer {token}'}
        if tenantid: headers['x-rks-tenantid'] = tenantid
        r = requests.get(url, headers=headers)
        print('getSyslogServerProfiles-- ',r)
        return r.json()

    #get VLAN Pools
    def getVlanPools(self, token, tenantid=None):
        url = f"https://{self.host}/vlanPools"
        headers={'Content-Type': 'application/json','Authorization': f'Bearer {token}'}
        if tenantid: headers['x-rks-tenantid'] = tenantid
        r = requests.get(url, headers=headers)
        print('getVlanPools-- ',r)
        return r.json()

    #get mDNS profiles
    def getmDnsProxyServiceProfiles(self, token, tenantid=None):
        url = f"https://{self.host}/mDnsProxyServiceProfiles"
        headers={'Content-Type': 'application/json','Authorization': f'Bearer {token}'}
        if tenantid: headers['x-rks-tenantid'] = tenantid
        r = requests.get(url, headers=headers)
        print('getmDnsProxyServiceProfiles-- ',r)
        return r.json()

    #get Enhanced mDNS profiles
    def getEnhancedMdnsProxyProfiles(self, token, payload, tenantid=None):
        url = f"https://{self.host}/enhancedMdnsProxyProfiles/query"
        headers={'Content-Type': 'application/json','Authorization': f'Bearer {token}'}
        if tenantid: headers['x-rks-tenantid'] = tenantid
        r = requests.post(url, json=payload, headers=headers)
        print('getEnhancedMdnsProxyProfiles-- ',r)
        return r.json()

    #get snmp agents
    def getSnmpAgents(self, token, payload, tenantid=None):
        url = f"https://{self.host}/snmpAgents/query"
        headers={'Content-Type': 'application/json','Authorization': f'Bearer {token}'}
        if tenantid: headers['x-rks-tenantid'] = tenantid
        r = requests.post(url, json=payload, headers=headers)
        print('getSnmpAgents-- ',r)
        return r.json()    

    #get DHCP profiles
    def getdhcpConfigServiceProfiles(self, token, tenantid=None):
        url = f"https://{self.host}/dhcpConfigServiceProfiles"
        headers={'Content-Type': 'application/json','Authorization': f'Bearer {token}'}
        if tenantid: headers['x-rks-tenantid'] = tenantid
        r = requests.get(url, headers=headers)
        print('getdhcpConfigServiceProfiles-- ',r)
        return r.json()

    #get RADIUS profiles by customer
    #required components: 
    def getRADIUSprofiles(self, token, tenantid=None):
        url = f"https://{self.host}/radiusServerProfiles"
        headers={'Content-Type': 'application/json','Authorization': f'Bearer {token}'}
        if tenantid: headers['x-rks-tenantid'] = tenantid
        r = requests.get(url, headers=headers)
        print('getRADIUSprofiles-- ',r)
        return r.json()

    #get DPSK service
    #required components:
    def getdpskServices(self, token, payload, tenantid=None):
        url = f"https://{self.host}/dpskServices/query"
        headers={'Content-Type': 'application/json','Authorization': f'Bearer {token}'}
        if tenantid: headers['x-rks-tenantid'] = tenantid
        r = requests.post(url, json=payload, headers=headers)
        print('getdpskServices -- ',r)
        return r.json()

    #get Switch profile
    #required components:  
    def getSwitchProfiles(self, token, tenantid=None):
        url = f"https://{self.host}/switchProfiles"
        headers={'Content-Type': 'application/json','Authorization': f'Bearer {token}'}
        if tenantid: headers['x-rks-tenantid'] = tenantid
        r = requests.get(url, headers=headers)
        print('getSwitchprofiles -- ',r)
        return r.json()

    #get Wifi Calling profile
    #required components:  
    def getWifiCallingServiceProfiles(self, token, tenantid=None):
        url = f"https://{self.host}/wifiCallingServiceProfiles"
        headers={'Content-Type': 'application/json','Authorization': f'Bearer {token}'}
        if tenantid: headers['x-rks-tenantid'] = tenantid
        r = requests.get(url, headers=headers)
        print('getWifiCallingServiceProfiles -- ',r)
        return r.json()

    #create a Customer under an MSP account
    #Required components: name, ???
    def createCustomer(self, token, payload):
        url = f"https://{self.host}/mspCustomers"
        headers={'Content-Type': 'application/json','Authorization': f'Bearer {token}'}
        r = requests.post(url, json=payload, headers=headers)
        print('createCustomer -- ',r)
        #print(r.json())
        if r.status_code == 202:
            ready_r = self.await_async_response(token, r)
            return ready_r.json()
        else: return r.json()
    
    #create a new Venue
    #Required components:  name, country, city
    #Optional for MSP entity: tenantid
    def createVenue(self, token, payload, tenantid=None, andwait=False):
        url = f"https://{self.host}/venues"
        headers={'Content-Type': 'application/json','Authorization': f'Bearer {token}'}
        if tenantid: headers['x-rks-tenantid'] = tenantid
        r = requests.post(url, json=payload, headers=headers)
        print('createVenue -- ',r)
        if andwait and r.status_code == 202:
            ready_r = self.await_async_response(token, r, tenantid)
            print('createVenueReady --', ready_r)
            return ready_r.json()
        else: return r.json()
        
    #create a new Network
    #required components:
    def createNetwork(self, token, payload, tenantid=None, andwait=False):
        url = f"https://{self.host}/networks"
        headers={'Content-Type': 'application/json','Authorization': f'Bearer {token}'}
        if tenantid: headers['x-rks-tenantid'] = tenantid
        r = requests.post(url, json=payload, headers=headers)
        print('createNetwork -- ', r)
        if andwait and r.status_code == 202:
            ready_r = self.await_async_response(token, r, tenantid)
            print('createNetworkReady -- ',ready_r)
            return ready_r.json()
        else: return r.json()

    #create AP Group
    #required components:  name
    def createAPgroup(self, token, payload, venueid, tenantid=None, andwait=False):
        url = f"https://{self.host}/venues/{venueid}/apGroups"
        headers={'Content-Type': 'application/json','Authorization': f'Bearer {token}'}
        if tenantid: headers['x-rks-tenantid'] = tenantid
        r = requests.post(url, json=payload, headers=headers)
        print('createAPgroup -- ',r)
        if andwait and r.status_code == 202:
            ready_r = self.await_async_response(token, r, tenantid)
            print('createAPgroupReady -- ',ready_r)
            return ready_r.json()
        else: return r.json()

    #create RADIUS profile
    #required components:  
    def createRADIUSprofile(self, token, payload, tenantid=None):
        url = f"https://{self.host}/radiusServerProfiles"
        headers={'Content-Type': 'application/json','Authorization': f'Bearer {token}'}
        if tenantid: headers['x-rks-tenantid'] = tenantid
        r = requests.post(url, json=payload, headers=headers)
        print('createRADIUSprofile -- ',r)
        return r.json()

    #create L2 ACL profile
    #required components:  
    def createL2AclPolicy(self, token, payload, tenantid=None):
        url = f"https://{self.host}/l2AclPolicies"
        headers={'Content-Type': 'application/json','Authorization': f'Bearer {token}'}
        if tenantid: headers['x-rks-tenantid'] = tenantid
        r = requests.post(url, json=payload, headers=headers)
        print('createL2AclPolicy -- ',r)
        return r.json()

    #create L3 ACL profile
    #required components:  
    def createL3AclPolicy(self, token, payload, tenantid=None):
        url = f"https://{self.host}/l3AclPolicies"
        headers={'Content-Type': 'application/json','Authorization': f'Bearer {token}'}
        if tenantid: headers['x-rks-tenantid'] = tenantid
        r = requests.post(url, json=payload, headers=headers)
        print('createL3AclPolicy -- ',r)
        return r.json()

    #create device profile
    #required components:  
    def createDevicePolicy(self, token, payload, tenantid=None):
        url = f"https://{self.host}/devicePolicies"
        headers={'Content-Type': 'application/json','Authorization': f'Bearer {token}'}
        if tenantid: headers['x-rks-tenantid'] = tenantid
        r = requests.post(url, json=payload, headers=headers)
        print('createDevicePolicy -- ',r)
        return r.json()

    #create application profile
    #required components:  
    def createApplicationPolicy(self, token, payload, tenantid=None):
        url = f"https://{self.host}/applicationPolicies"
        headers={'Content-Type': 'application/json','Authorization': f'Bearer {token}'}
        if tenantid: headers['x-rks-tenantid'] = tenantid
        r = requests.post(url, json=payload, headers=headers)
        print('createApplicationPolicy -- ',r)
        return r.json()

    #create access control profile
    #required components:  
    def createAccessControlProfile(self, token, payload, tenantid=None):
        url = f"https://{self.host}/accessControlProfiles"
        headers={'Content-Type': 'application/json','Authorization': f'Bearer {token}'}
        if tenantid: headers['x-rks-tenantid'] = tenantid
        r = requests.post(url, json=payload, headers=headers)
        print('createAccessControlProfile -- ',r)
        return r.json()

    #create client isolation profile
    #required components:  
    def createClientIsolationAllowlist(self, token, payload, tenantid=None):
        url = f"https://{self.host}/isolationAllowlists"
        headers={'Content-Type': 'application/json','Authorization': f'Bearer {token}'}
        if tenantid: headers['x-rks-tenantid'] = tenantid
        r = requests.post(url, json=payload, headers=headers)
        print('createIsolationAllowlist -- ',r)
        return r.json()

    #create rogue ap policy profile
    #required components:  
    def createRogueApPolicyProfile(self, token, payload, tenantid=None):
        url = f"https://{self.host}/rogueApPolicyProfiles"
        headers={'Content-Type': 'application/json','Authorization': f'Bearer {token}'}
        if tenantid: headers['x-rks-tenantid'] = tenantid
        r = requests.post(url, json=payload, headers=headers)
        print('createRogueApPolicyProfile -- ',r)
        return r.json()

    #create syslog profile
    #required components:  
    def createSyslogServerProfile(self, token, payload, tenantid=None):
        url = f"https://{self.host}/syslogServerProfiles"
        headers={'Content-Type': 'application/json','Authorization': f'Bearer {token}'}
        if tenantid: headers['x-rks-tenantid'] = tenantid
        r = requests.post(url, json=payload, headers=headers)
        print('createSyslogServerProfile -- ',r)
        return r.json()

    #create vlan pool
    #required components:  
    def createVlanPool(self, token, payload, tenantid=None):
        url = f"https://{self.host}/vlanPools"
        headers={'Content-Type': 'application/json','Authorization': f'Bearer {token}'}
        if tenantid: headers['x-rks-tenantid'] = tenantid
        r = requests.post(url, json=payload, headers=headers)
        print('createVlanPool -- ',r)
        return r.json()

    #create snmp agents
    #required components:  
    def createApSnmpAgentProfiles(self, token, payload, tenantid=None):
        url = f"https://{self.host}/apSnmpAgentProfiles"
        headers={'Content-Type': 'application/json','Authorization': f'Bearer {token}'}
        if tenantid: headers['x-rks-tenantid'] = tenantid
        r = requests.post(url, json=payload, headers=headers)
        print('createApSnmpAgentProfiles -- ',r)
        return r.json()

    #create mdns profile
    #required components:  
    def createmDnsProxyServiceProfile(self, token, payload, tenantid=None):
        url = f"https://{self.host}/mDnsProxyServiceProfiles"
        headers={'Content-Type': 'application/json','Authorization': f'Bearer {token}'}
        if tenantid: headers['x-rks-tenantid'] = tenantid
        r = requests.post(url, json=payload, headers=headers)
        print('createmDnsProxyServiceProfiles -- ',r)
        return r.json()

    #create dhcp profile
    #required components:  
    def createDhcpConfigServiceProfile(self, token, payload, tenantid=None):
        url = f"https://{self.host}/dhcpConfigServiceProfiles"
        headers={'Content-Type': 'application/json','Authorization': f'Bearer {token}'}
        if tenantid: headers['x-rks-tenantid'] = tenantid
        r = requests.post(url, json=payload, headers=headers)
        print('createdhcpConfigServiceProfiles -- ',r)
        return r.json()

    #create DPSK service
    #required components:
    def createdpskServices(self, token, payload, tenantid=None):
        url = f"https://{self.host}/dpskServices"
        headers={'Content-Type': 'application/json','Authorization': f'Bearer {token}'}
        if tenantid: headers['x-rks-tenantid'] = tenantid
        r = requests.post(url, json=payload, headers=headers)
        print('createdpskServices -- ',r)
        return r.json()

    #create Switch profile
    #required components:  
    def createSwitchProfiles(self, token, payload, tenantid=None):
        url = f"https://{self.host}/switchProfiles"
        headers={'Content-Type': 'application/json','Authorization': f'Bearer {token}'}
        if tenantid: headers['x-rks-tenantid'] = tenantid
        r = requests.post(url, json=payload, headers=headers)
        print('createSwitchprofile -- ',r)
        return r.json()

    #create wifi calling profile
    #required components:  
    def createWifiCallingServiceProfiles(self, token, payload, tenantid=None):
        url = f"https://{self.host}/wifiCallingServiceProfiles"
        headers={'Content-Type': 'application/json','Authorization': f'Bearer {token}'}
        if tenantid: headers['x-rks-tenantid'] = tenantid
        r = requests.post(url, json=payload, headers=headers)
        print('createWifiCallingServicerofile -- ',r)
        return r.json()

    #update Venue Load Balancing
    def updateVenueLoadBalancingSettings(self, token, payload, venueId=None, tenantid=None):
        url = f"https://{self.host}/venues/{venueId}/loadBalancingSettings"
        headers={'Content-Type': 'application/json','Authorization': f'Bearer {token}'}
        if tenantid: headers['x-rks-tenantid'] = tenantid
        r = requests.put(url, json=payload, headers=headers)
        print('updateLoadBalancingSettings -- ',r)
        return r.json()

    #update Venue Radio Settings
    def updateVenueRadioSettings(self, token, payload, venueId=None, tenantid=None):
        url = f"https://{self.host}/venues/{venueId}/radioSettings"
        headers={'Content-Type': 'application/json','Authorization': f'Bearer {token}'}
        if tenantid: headers['x-rks-tenantid'] = tenantid
        r = requests.put(url, json=payload, headers=headers)
        print('updateRadioSettings -- ',r)
        return r.json()

    #update Venue Directed Multicast
    def updateVenueDirectedMulticastSettings(self, token, payload, venueId=None, tenantid=None):
        url = f"https://{self.host}/venues/{venueId}/directedMulticastSettings"
        headers={'Content-Type': 'application/json','Authorization': f'Bearer {token}'}
        if tenantid: headers['x-rks-tenantid'] = tenantid
        r = requests.put(url, json=payload, headers=headers)
        print('updateDirectedMulticastSettings -- ',r)
        return r.json()

    #update Venue Mesh Settings
    def updateVenueMeshSettings(self, token, payload, venueId=None, tenantid=None):
        url = f"https://{self.host}/venues/{venueId}/meshSettings"
        headers={'Content-Type': 'application/json','Authorization': f'Bearer {token}'}
        if tenantid: headers['x-rks-tenantid'] = tenantid
        r = requests.post(url, json=payload, headers=headers)
        print('updateMeshSettings -- ',r)
        return r.json()

    #update Venue Rogue Settings
    def updateVenueRogueSettings(self, token, payload, venueId=None, tenantid=None):
        url = f"https://{self.host}/venues/{venueId}/rogueApSettings"
        headers={'Content-Type': 'application/json','Authorization': f'Bearer {token}'}
        if tenantid: headers['x-rks-tenantid'] = tenantid
        r = requests.post(url, json=payload, headers=headers)
        print('updateRogueApSettings -- ',r)
        return r.json()

    #update Venue DOS Protection Settings
    def updateVenueDosProtectionSettings(self, token, payload, venueId=None, tenantid=None):
        url = f"https://{self.host}/venues/{venueId}/dosProtectionSettings"
        headers={'Content-Type': 'application/json','Authorization': f'Bearer {token}'}
        if tenantid: headers['x-rks-tenantid'] = tenantid
        r = requests.post(url, json=payload, headers=headers)
        print('updateDosProtectionSettings -- ',r)
        return r.json()
    
    #activate a single network
    #required components: 
    def activateNetwork(self, token, payload, tenantid=None):
        url = f"https://{self.host}/networkActivations"
        headers={'Content-Type': 'application/json','Authorization': f'Bearer {token}'}
        if tenantid: headers['x-rks-tenantid'] = tenantid
        r = requests.post(url, json=payload, headers=headers)
        print('activateNetwork -- ',r)
        return r.json()
        
    #activate bulk networks
    #required components: 
    def activateNetworks(self, token, payload, tenantid=None):
        url = f"https://{self.host}/networkActivations/mappings"
        headers={'Content-Type': 'application/json','Authorization': f'Bearer {token}'}
        if tenantid: headers['x-rks-tenantid'] = tenantid
        r = requests.post(url, json=payload, headers=headers)
        print('activateNetworks -- ',r)
        return r.json()    
        
    #delay function for giving async responses a little more time
    def await_async_response(self, token, orig_r, tenantid=None, sleep_time=3, cycles=7):
        """Check up on the status of the async call."""
        if orig_r.status_code != 202:
            return orig_r
        requestId = orig_r.json()['requestId']
        print('\nWaiting for request to complete:', requestId)
        url = f"https://{self.host}/activities/{requestId}"
        headers = {'Content-Type': 'application/json','Authorization': f'Bearer {token}'}
        if tenantid: headers['x-rks-tenantid'] = tenantid
        attempt = 0
        while True:
            if attempt > cycles: break
            try:
                r = requests.get(url, verify=False, headers=headers)
                #print(r)
                print('request:', r, r.json())
                print('orig:', orig_r, orig_r.json())
                resp = r.json()
                if resp['status'] in ['SUCCESS', 'FAIL']:
                    break
                time.sleep(sleep_time)
            except Exception as e:
                print(e)
                print('retrying')
                time.sleep(sleep_time)
            attempt += 1
        if resp['status'] != 'SUCCESS':
            #raise Exception(r.status)
            return orig_r
        else:
            return orig_r

    #delete a venue
    def deleteVenue(self, token, venueid, tenantid):
        url = f"https://{self.host}/venues/{venueid}"
        headers={'Content-Type': 'application/json','Authorization': f'Bearer {token}'}
        if tenantid: headers['x-rks-tenantid'] = tenantid
        r = requests.delete(url, headers=headers)
        print('deleteVenue -- ',r)
        return r.json()  