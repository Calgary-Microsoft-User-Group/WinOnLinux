// WinOnLinux Linux dev VM — Ubuntu LTS + GNOME desktop over xrdp.
// Deployed by deploy.ps1. See README.md in this directory.

@description('Name of the virtual machine.')
param vmName string = 'vm-wol-dev'

@description('Azure region.')
param location string = resourceGroup().location

@description('Admin username (used for SSH and the xrdp desktop login).')
param adminUsername string = 'woldev'

@description('Admin password. Azure rules: 12-72 chars, 3 of 4 character classes.')
@secure()
param adminPassword string

@description('VM size. AZ-PRI-01 has zero Dsv5-family quota in canadacentral; Dsv4 has 10 cores.')
param vmSize string = 'Standard_D4s_v4'

@description('Public IP or CIDR allowed to reach SSH (22) and RDP (3389).')
param allowedSourceIp string

@description('Marketplace image offer. NFR-7: current or previous Ubuntu LTS.')
param imageOffer string = 'ubuntu-24_04-lts'

@description('Marketplace image SKU (Gen2).')
param imageSku string = 'server'

@description('OS disk size in GB.')
param osDiskSizeGb int = 128

@description('Daily auto-shutdown time, HHmm, in autoShutdownTimeZone.')
param autoShutdownTime string = '1900'

@description('Time zone for the auto-shutdown schedule.')
param autoShutdownTimeZone string = 'Mountain Standard Time'

var nsgName = 'nsg-${vmName}'
var vnetName = 'vnet-${vmName}'
var pipName = 'pip-${vmName}'
var nicName = 'nic-${vmName}'
var osDiskName = '${vmName}-osdisk'

resource nsg 'Microsoft.Network/networkSecurityGroups@2024-05-01' = {
  name: nsgName
  location: location
  properties: {
    securityRules: [
      {
        name: 'Allow-SSH-From-Home'
        properties: {
          priority: 1000
          direction: 'Inbound'
          access: 'Allow'
          protocol: 'Tcp'
          sourceAddressPrefix: allowedSourceIp
          sourcePortRange: '*'
          destinationAddressPrefix: '*'
          destinationPortRange: '22'
        }
      }
      {
        name: 'Allow-RDP-From-Home'
        properties: {
          priority: 1010
          direction: 'Inbound'
          access: 'Allow'
          protocol: 'Tcp'
          sourceAddressPrefix: allowedSourceIp
          sourcePortRange: '*'
          destinationAddressPrefix: '*'
          destinationPortRange: '3389'
        }
      }
    ]
  }
}

resource vnet 'Microsoft.Network/virtualNetworks@2024-05-01' = {
  name: vnetName
  location: location
  properties: {
    addressSpace: {
      addressPrefixes: ['10.20.0.0/24']
    }
    subnets: [
      {
        name: 'snet-dev'
        properties: {
          addressPrefix: '10.20.0.0/26'
          networkSecurityGroup: { id: nsg.id }
        }
      }
    ]
  }
}

resource pip 'Microsoft.Network/publicIPAddresses@2024-05-01' = {
  name: pipName
  location: location
  sku: { name: 'Standard' }
  properties: {
    publicIPAllocationMethod: 'Static'
  }
}

resource nic 'Microsoft.Network/networkInterfaces@2024-05-01' = {
  name: nicName
  location: location
  properties: {
    ipConfigurations: [
      {
        name: 'ipconfig1'
        properties: {
          subnet: { id: vnet.properties.subnets[0].id }
          privateIPAllocationMethod: 'Dynamic'
          publicIPAddress: { id: pip.id }
        }
      }
    ]
  }
}

// securityProfile deliberately omitted (Standard security, not Trusted Launch) so the
// archive-to-cool.ps1 blob round-trip can recreate the VM from a plain imported disk.
resource vm 'Microsoft.Compute/virtualMachines@2024-07-01' = {
  name: vmName
  location: location
  properties: {
    hardwareProfile: { vmSize: vmSize }
    storageProfile: {
      imageReference: {
        publisher: 'Canonical'
        offer: imageOffer
        sku: imageSku
        version: 'latest'
      }
      osDisk: {
        name: osDiskName
        createOption: 'FromImage'
        diskSizeGB: osDiskSizeGb
        managedDisk: { storageAccountType: 'StandardSSD_LRS' }
        deleteOption: 'Detach'
      }
    }
    osProfile: {
      computerName: vmName
      adminUsername: adminUsername
      adminPassword: adminPassword
      customData: loadFileAsBase64('cloud-init.yaml')
      linuxConfiguration: {
        disablePasswordAuthentication: false
        provisionVMAgent: true
      }
    }
    networkProfile: {
      networkInterfaces: [
        { id: nic.id, properties: { deleteOption: 'Detach' } }
      ]
    }
    diagnosticsProfile: {
      bootDiagnostics: { enabled: true }
    }
  }
}

resource autoShutdown 'Microsoft.DevTestLab/schedules@2018-09-15' = {
  name: 'shutdown-computevm-${vmName}'
  location: location
  properties: {
    status: 'Enabled'
    taskType: 'ComputeVmShutdownTask'
    dailyRecurrence: { time: autoShutdownTime }
    timeZoneId: autoShutdownTimeZone
    notificationSettings: { status: 'Disabled' }
    targetResourceId: vm.id
  }
}

output publicIpAddress string = pip.properties.ipAddress
output adminUsername string = adminUsername
output osDiskName string = osDiskName
