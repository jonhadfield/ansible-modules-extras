#!/usr/bin/python
# This file is part of Ansible
#
# Ansible is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Ansible is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with Ansible.  If not, see <http://www.gnu.org/licenses/>.


DOCUMENTATION = '''
---
module: ec2_vpc_nat_gateway
short_description: Manage AWS VPC NAT Gateways
description:
  - Ensure the state of AWS VPC NAT Gateways based on their id, allocation and subnet ids.
version_added: "2.2"
options:
  state:
    description:
      - Ensure NAT Gateway is present or absent
    required: false
    default: "present"
    choices: ["present", "absent"]
  nat_gateway_id:
    description:
      - The id AWS dynamically allocates to the NAT Gateway on creation
    required: false
    default: None
  subnet_id:
    description:
      - The id of the subnet to create the NAT Gateway in
    required: false
    default: None
  allocation_id:
    description:
      - The id of the elastic IP allocation
    required: false
    default: None
  max_count:
    description:
      - The maximum number of NAT Gateways that should exist in the specified subnet after the task has completed
    required: false
    default: 1
  wait:
    description:
      - Wait for operation to complete before returning
    required: false
    default: true
  wait_timeout:
    description:
      - How many seconds to wait for an operation to complete before timing out
    required: false
    default: 300

author:
  - "Jon Hadfield (@jonhadfield)"
  - "Karen Cheng(@Etherdaemon)"
extends_documentation_fragment:
  - aws
  - ec2
'''

EXAMPLES = '''
# Note: These examples do not set authentication details, see the AWS Guide for details.

# Ensure that a VPC NAT Gateway exists in a subnet by passing the subnet id and EIP allocation id
ec2_vpc_nat_gateway:
  state: present
  subnet_id: subnet-a67643d1
  allocation_id: eipalloc-a32baec6

# Ensure no more than two VPC NAT Gateways exist in the specified subnet once the task has completed
ec2_vpc_nat_gateway:
  state: present
  subnet_id: subnet-a67643d1
  allocation_id: eipalloc-a32baec6
  max_count: 2

# Ensure that a VPC NAT Gateway exists in a subnet by passing the subnet id and
# EIP allocation id but do not wait for operation to complete before continuing
ec2_vpc_nat_gateway:
  state: present
  subnet_id: subnet-a67643d1
  allocation_id: eipalloc-a32baec6
  wait: false

# Ensure that a VPC NAT Gateway identified by id does not exist
ec2_vpc_nat_gateway:
  state: absent
  nat_gateway_id: nat-0d1e3a878585988f8
'''

RETURN = '''
nat_gateway:
  description: details of the nat gateway created
  returned: when created
  type: dict
  sample:  {
    "nat_gateway": {
      "create_time": "2016-05-02T21:21:50.683000+00:00",
      "nat_gateway_addresses": [
        {
        "allocation_id": "eipalloc-784f391d"
        }
      ],
      "nat_gateway_id": "nat-03afc583f2b2962b6",
      "state": "pending",
      "subnet_id": "subnet-16640561",
      "vpc_id": "vpc-6a31bc0f"
    }
  }
nat_gateway_id:
    description: id of the VPC NAT Gateway
    returned: On create when NAT GW already exists and on completed deletion
    type: string
    sample: "nat-0d1e3a878585988f8"
'''

import datetime
import time

try:
    import boto3
    import botocore

    HAS_BOTO_3 = True
except ImportError:
    HAS_BOTO_3 = False
from distutils.version import LooseVersion

if LooseVersion(botocore.__version__) < LooseVersion("1.3.14"):
    HAS_SUFFICIENT_BOTOCORE = False
else:
    HAS_SUFFICIENT_BOTOCORE = True


def get_nat_gateway_status_list(ec2_client=None, module=None):
    """ Check if one or more NAT gateways exist with the specified attributes and return a list of matching """
    try:
        subnet_id = module.params.get('subnet_id')
        allocation_id = module.params.get('allocation_id')
        # We only pass nat_gateway_id alone if we are ensuring it is absent
        if module.params.get('nat_gateway_id'):
            result = ec2_client.describe_nat_gateways(NatGatewayIds=[module.params.get('nat_gateway_id')])
            return [{'state': result.get('NatGateways')[0].get('State'),
                     'created': result.get('NatGateways')[0].get('CreateTime')}]
        # Checking if VPC NAT Gateway matching attributes is present
        elif all((subnet_id, allocation_id)):
            result = ec2_client.describe_nat_gateways(Filter=[
                {
                    'Name': 'subnet-id',
                    'Values': [subnet_id]
                }
            ])
            if result.get('NatGateways'):
                gateway_list = list()
                for nat_gateway in result.get('NatGateways'):
                    for nat_gateway_address in nat_gateway.get('NatGatewayAddresses'):
                        if nat_gateway_address.get('AllocationId') == allocation_id:
                            gateway_list.append({'state': nat_gateway.get('State'),
                                                 'created': nat_gateway.get('CreateTime'),
                                                 'nat_gateway_id': nat_gateway.get('NatGatewayId')})
                return gateway_list
        return [{'state': 'absent'}]
    except botocore.exceptions.ClientError as ce:
        if ce.response['Error']['Code'] == "NatGatewayNotFound":
            return [{'state': 'absent'}]
        else:
            module.fail_json(msg=ce.message, **camel_dict_to_snake_dict(ce.response))


def delete_nat_gateway(ec2_client=None, module=None, nat_gateway=None):
    """ Delete an existing NAT gateway """
    nat_gateway_address = nat_gateway.get('NatGatewayAddresses')[0]
    nat_gateway_id = nat_gateway['NatGatewayId']
    results = dict(changed=True, nat_gateway_id=nat_gateway_id,
                   public_ip=nat_gateway_address.get('PublicIp'),
                   private_ip=nat_gateway_address.get('PrivateIp'),
                   allocation_id=nat_gateway_address.get('AllocationId'))
    ec2_client.delete_nat_gateway(NatGatewayId=nat_gateway_id)

    wait = module.params.get('wait')
    if wait:
        wait_timeout = time.time() + module.params.get('wait_timeout')
        while wait_timeout > time.time():
            nat_gateway_status_list = get_nat_gateway_status_list(ec2_client=ec2_client, module=module)
            if nat_gateway_status_list[0].get('state') in ('deleted', 'absent'):
                module.exit_json(**results)
            else:
                time.sleep(5)
        module.fail_json(msg="Waited too long for VPC NAT Gateway to be deleted.")
    else:
        module.exit_json(**results)


def create_nat_gateway(ec2_client=None, module=None):
    """ Create the NAT gateway """
    try:
        result = ec2_client.create_nat_gateway(SubnetId=module.params.get('subnet_id'),
                                               AllocationId=module.params.get('allocation_id'))
        wait = module.params.get('wait')
        start_time = datetime.datetime.utcnow()
        results = dict(changed=True)
        if wait:
            wait_timeout = time.time() + module.params.get('wait_timeout')
            while wait_timeout > time.time():
                if 'nat_gateway' in results:
                    break
                for present_status in get_nat_gateway_status_list(ec2_client=ec2_client, module=module):
                    create_time = present_status.get('created')
                    if present_status.get('state') == 'failed' and create_time.replace(tzinfo=None) >= start_time:
                        module.fail_json(msg="Failed to create VPC NAT Gateway")
                    elif present_status.get('state') == 'available':
                        results['nat_gateway'] = camel_dict_to_snake_dict(result['NatGateway'])
                        break
                    else:
                        time.sleep(5)
            else:
                module.fail_json(msg="Waited too long for VPC NAT Gateway to be created.")
        module.exit_json(**results)
    except botocore.exceptions.ClientError as ce:
        module.fail_json(msg=ce.message, **camel_dict_to_snake_dict(ce.response))


def ensure_nat_gateway_absent(ec2_client=None, module=None):
    """ Ensure the specified NAT gateway does not exist and call delete if it does """
    try:
        results = dict(changed=False)
        nat_gateways_result = ec2_client.describe_nat_gateways(NatGatewayIds=[module.params.get('nat_gateway_id')])
        if nat_gateways_result:
            nat_gateway = nat_gateways_result.get('NatGateways')[0]
            if nat_gateway.get('State') in ('pending',
                                            'available'):
                if module.check_mode:
                    results['changed'] = True
                    module.exit_json(**results)
                else:
                    delete_nat_gateway(ec2_client=ec2_client,
                                       module=module,
                                       nat_gateway=nat_gateway)
        module.exit_json(**results)
    except botocore.exceptions.ClientError as ce:
        module.fail_json(msg=ce.message, **camel_dict_to_snake_dict(ce.response))


def ensure_nat_gateway_present(ec2_client=None, module=None):
    """ Ensure NAT gateway with specified parameters exists and call create if it does not """
    # Check allocation-id refers to existing EIP allocation
    allocation_id = module.params.get('allocation_id')
    try:
        ec2_client.describe_addresses(AllocationIds=[allocation_id])

        # Check subnet exists
        subnet_id = module.params.get('subnet_id')
        ec2_client.describe_subnets(SubnetIds=[subnet_id])

        # Get existing number of NAT Gateways in specified subnet
        nat_gateways = ec2_client.describe_nat_gateways(Filters=[{'Name': 'subnet-id',
                                                                  'Values': [subnet_id]},
                                                                 {'Name': 'state',
                                                                  'Values': ['available', 'pending',
                                                                             'deleting']}])
        num_nat_gateways = len(nat_gateways.get('NatGateways', []))

        # Check max_count has not been exceeded before attempting to create
        if module.params.get('max_count') < num_nat_gateways:
            module.fail_json(
                msg="The maximum desired number of NAT Gateways specified in "
                    "max_count has already been exceeded.")
        gateway_status_list = get_nat_gateway_status_list(ec2_client=ec2_client, module=module)
        for gateway_status in gateway_status_list:
            if gateway_status.get('state') == 'available':
                results = dict(changed=False, nat_gateway_id=gateway_status['nat_gateway_id'])
                module.exit_json(**results)
        else:
            # Check creation of NAT Gateway does not exceed max_count
            if module.params.get('max_count') == num_nat_gateways:
                module.fail_json(
                    msg="The maximum desired number of NAT Gateways specified in "
                        "max_count (default: 1) has already been met.")
            if not module.check_mode:
                create_nat_gateway(ec2_client=ec2_client, module=module)
            else:
                module.exit_json(dict(change=True))
    except botocore.exceptions.ClientError as ce:
        module.fail_json(msg=ce.message, **camel_dict_to_snake_dict(ce.response))


def main():
    argument_spec = ec2_argument_spec()
    argument_spec.update(dict(
        state=dict(default='present', choices=['present', 'absent']),
        subnet_id=dict(required=False, type='str'),
        allocation_id=dict(required=False, type='str'),
        nat_gateway_id=dict(required=False, type='str'),
        wait=dict(required=False, default=True, type='bool'),
        max_count=dict(required=False, default=1, type='int'),
        wait_timeout=dict(required=False, default=300, type='int')))

    module = AnsibleModule(argument_spec=argument_spec,
                           supports_check_mode=True,
                           required_if=[
                               ('state', 'present', ['subnet_id', 'allocation_id']),
                               ('state', 'absent', ['nat_gateway_id'])
                           ],
                           mutually_exclusive=[
                               ['nat_gateway_id', 'max_count']
                           ])

    try:
        if not HAS_BOTO_3:
            module.fail_json(msg='boto3 and botocore are required.')
        if not HAS_SUFFICIENT_BOTOCORE:
            module.fail_json(msg='botocore version 1.3.14 or above is required.')

        region, ec2_url, aws_connect_kwargs = get_aws_connection_info(module, boto3=True)

        ec2_client = boto3_conn(module,
                                conn_type='client',
                                resource='ec2',
                                region=region,
                                endpoint=ec2_url,
                                **aws_connect_kwargs)

        if module.params['state'] == 'absent':
            ensure_nat_gateway_absent(ec2_client=ec2_client, module=module)
        else:
            ensure_nat_gateway_present(ec2_client=ec2_client, module=module)
    except botocore.exceptions.NoRegionError as nre:
        module.fail_json(msg=nre.message, **camel_dict_to_snake_dict(nre.response))
    except botocore.exceptions.NoCredentialsError as nce:
        module.fail_json(msg=nce.message)


from ansible.module_utils.basic import *
from ansible.module_utils.ec2 import *

if __name__ == "__main__":
    main()
