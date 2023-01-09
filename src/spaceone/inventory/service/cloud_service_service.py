import copy
from datetime import datetime

from spaceone.core.service import *
from spaceone.core import utils
from spaceone.inventory.model.cloud_service_model import CloudService, CollectionInfo
from spaceone.inventory.manager.cloud_service_manager import CloudServiceManager
from spaceone.inventory.manager.region_manager import RegionManager
from spaceone.inventory.manager.identity_manager import IdentityManager
from spaceone.inventory.manager.resource_group_manager import ResourceGroupManager
from spaceone.inventory.manager.change_history_manager import ChangeHistoryManager
from spaceone.inventory.manager.collection_state_manager import CollectionStateManager
from spaceone.inventory.manager.note_manager import NoteManager
from spaceone.inventory.error import *

_KEYWORD_FILTER = ['cloud_service_id', 'name', 'ip_addresses', 'cloud_service_group', 'cloud_service_type',
                   'reference.resource_id']


@authentication_handler
@authorization_handler
@mutation_handler
@event_handler
class CloudServiceService(BaseService):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.cloud_svc_mgr: CloudServiceManager = self.locator.get_manager('CloudServiceManager')
        self.region_mgr: RegionManager = self.locator.get_manager('RegionManager')
        self.identity_mgr: IdentityManager = self.locator.get_manager('IdentityManager')
        self.collector_id = self.transaction.get_meta('collector_id')
        self.job_id = self.transaction.get_meta('job_id')
        self.plugin_id = self.transaction.get_meta('plugin_id')
        self.service_account_id = self.transaction.get_meta('secret.service_account_id')

    @transaction(append_meta={
        'authorization.scope': 'PROJECT',
        'authorization.require_project_id': True
    })
    @check_required(['cloud_service_type', 'cloud_service_group', 'provider', 'data', 'domain_id'])
    def create(self, params):
        """
        Args:
            params (dict): {
                    'cloud_service_type': 'str',
                    'cloud_service_group': 'str',
                    'provider': 'str',
                    'name': 'str',
                    'account': 'str',
                    'instance_type': 'str',
                    'instance_size': 'float',
                    'ip_addresses': 'list',
                    'data': 'dict',
                    'metadata': 'dict',
                    'reference': 'dict',
                    'tags': 'list or dict',
                    'region_code': 'str',
                    'project_id': 'str',
                    'domain_id': 'str'
                }

        Returns:
            cloud_service_vo (object)

        """

        ch_mgr: ChangeHistoryManager = self.locator.get_manager('ChangeHistoryManager')

        domain_id = params['domain_id']
        project_id = params.get('project_id')
        secret_project_id = self.transaction.get_meta('secret.project_id')
        provider = params.get('provider', self.transaction.get_meta('secret.provider'))

        params['provider'] = provider

        if params['provider'] is None:
            raise ERROR_REQUIRED_PARAMETER(key='provider')

        if 'tags' in params:
            params['tag_keys'] = self._create_tag_keys(params['tags'], provider)
            params['tags'] = self._create_tags(params['tags'], provider)

        if 'instance_size' in params:
            if not isinstance(params['instance_size'], float):
                raise ERROR_INVALID_PARAMETER_TYPE(key='instance_size', type='float')

        if project_id:
            self.identity_mgr.get_project(project_id, domain_id)
        elif secret_project_id:
            params['project_id'] = secret_project_id

        params['ref_cloud_service_type'] = self._make_cloud_service_type_key(params)

        if 'region_code' in params:
            params['ref_region'] = self._make_region_key(params, params['provider'])

        if 'metadata' in params:
            params['metadata'] = self._change_metadata_path(params['metadata'], provider)

        params['collection_info'] = self._get_collection_info(provider)

        cloud_svc_vo = self.cloud_svc_mgr.create_cloud_service(params)

        # Create New History
        ch_mgr.add_new_history(cloud_svc_vo, params)

        # Create Collection State
        state_mgr: CollectionStateManager = self.locator.get_manager('CollectionStateManager')
        state_mgr.create_collection_state(cloud_svc_vo.cloud_service_id, 'inventory.CloudService', domain_id)

        return cloud_svc_vo

    @transaction(append_meta={'authorization.scope': 'PROJECT'})
    @check_required(['cloud_service_id', 'domain_id'])
    def update(self, params):
        """
        Args:
            params (dict): {
                    'cloud_service_id': 'str',
                    'name': 'str',
                    'account': 'str',
                    'instance_type': 'str',
                    'instance_size': 'float',
                    'ip_addresses': 'list',
                    'data': 'dict',
                    'metadata': 'dict',
                    'reference': 'dict',
                    'tags': 'list or dict',
                    'region_code': 'str',
                    'project_id': 'str',
                    'domain_id': 'str',
                    'release_project': 'bool',
                    'release_region': 'bool'
                }

        Returns:
            cloud_service_vo (object)

        """

        ch_mgr: ChangeHistoryManager = self.locator.get_manager('ChangeHistoryManager')

        project_id = params.get('project_id')
        secret_project_id = self.transaction.get_meta('secret.project_id')

        cloud_service_id = params['cloud_service_id']
        domain_id = params['domain_id']
        release_region = params.get('release_region', False)
        release_project = params.get('release_project', False)
        provider = self._get_provider_from_meta()

        if 'ip_addresses' in params and params['ip_addresses'] is None:
            del params['ip_addresses']

        cloud_svc_vo: CloudService = self.cloud_svc_mgr.get_cloud_service(cloud_service_id, domain_id)

        if 'instance_size' in params:
            if not isinstance(params['instance_size'], float):
                raise ERROR_INVALID_PARAMETER_TYPE(key='instance_size', type='float')

        if release_project:
            params['project_id'] = None
        elif project_id:
            self.identity_mgr.get_project(project_id, domain_id)
        elif secret_project_id and secret_project_id != cloud_svc_vo.project_id:
            params['project_id'] = secret_project_id

        if release_region:
            params.update({
                'region_code': None,
                'ref_region': None
            })
        elif 'region_code' in params:
            params['ref_region'] = self._make_region_key(params, cloud_svc_vo.provider)

        old_cloud_svc_data = dict(cloud_svc_vo.to_dict())

        if 'tags' in params:
            old_tags = old_cloud_svc_data.get('tags', {})
            new_tags = self._create_tags(params['tags'], provider)
            old_tag_keys = old_cloud_svc_data.get('tag_keys', {})
            new_tag_keys = self._create_tag_keys(params['tags'], provider)

            if new_tags != old_tags:
                old_tags.update(new_tags)
                old_tag_keys.update(new_tag_keys)
                params['tags'] = old_tags
                params['tag_keys'] = old_tag_keys
            else:
                del params['tags']

        if 'metadata' in params:
            old_metadata = old_cloud_svc_data.get('metadata', {})
            new_metadata = self._change_metadata_path(params['metadata'], provider)

            if new_metadata != old_metadata:
                old_metadata.update(new_metadata)
                params['metadata'] = old_metadata
            else:
                del params['metadata']

        if 'collection_info' in params:
            old_collection_info = old_cloud_svc_data.get('collection_info', [])
            new_collection_info = self._get_collection_info(provider)

            if old_collection_info != new_collection_info:
                params['collection_info'] = self._merge_collection_info(old_collection_info, new_collection_info)
            else:
                del params['collection_info']

        params = self.cloud_svc_mgr.merge_data(params, old_cloud_svc_data)

        cloud_svc_vo = self.cloud_svc_mgr.update_cloud_service_by_vo(params, cloud_svc_vo)

        # Create Update History
        ch_mgr.add_update_history(cloud_svc_vo, params, old_cloud_svc_data)

        # Update Collection History
        state_mgr: CollectionStateManager = self.locator.get_manager('CollectionStateManager')
        state_vo = state_mgr.get_collection_state(cloud_service_id, domain_id)
        if state_vo:
            state_mgr.reset_collection_state(state_vo)
        else:
            state_mgr.create_collection_state(cloud_service_id, 'inventory.CloudService', domain_id)

        if 'project_id' in params:
            note_mgr: NoteManager = self.locator.get_manager('NoteManager')

            # Update Project ID from Notes
            note_vos = note_mgr.filter_notes(cloud_service_id=cloud_service_id, domain_id=domain_id)
            note_vos.update({'project_id': params['project_id']})

        return cloud_svc_vo

    @transaction(append_meta={'authorization.scope': 'PROJECT'})
    @check_required(['cloud_service_id', 'domain_id'])
    def delete(self, params):
        """
        Args:
            params (dict): {
                    'cloud_service_id': 'str',
                    'domain_id': 'str'
                }

        Returns:
            None

        """

        ch_mgr: ChangeHistoryManager = self.locator.get_manager('ChangeHistoryManager')

        cloud_service_id = params['cloud_service_id']
        domain_id = params['domain_id']

        cloud_svc_vo: CloudService = self.cloud_svc_mgr.get_cloud_service(cloud_service_id, domain_id)

        self.cloud_svc_mgr.delete_cloud_service_by_vo(cloud_svc_vo)

        # Create Update History
        ch_mgr.add_delete_history(cloud_svc_vo)

        # Cascade Delete Collection State
        state_mgr: CollectionStateManager = self.locator.get_manager('CollectionStateManager')
        state_mgr.delete_collection_state_by_resource_id(cloud_service_id, domain_id)

    @transaction(append_meta={'authorization.scope': 'PROJECT'})
    @check_required(['cloud_service_id', 'domain_id'])
    def get(self, params):
        """
        Args:
            params (dict): {
                    'cloud_service_id': 'str',
                    'domain_id': 'str',
                    'only': 'list'
                }

        Returns:
            cloud_service_vo (object)

        """

        return self.cloud_svc_mgr.get_cloud_service(params['cloud_service_id'], params['domain_id'],
                                                    only=params.get('only'))

    @transaction(append_meta={'authorization.scope': 'PROJECT'})
    @check_required(['domain_id'])
    @append_query_filter(['cloud_service_id', 'name', 'state', 'account', 'instance_type', 'cloud_service_type',
                          'cloud_service_group', 'provider', 'region_code', 'resource_group_id', 'project_id',
                          'project_group_id', 'domain_id', 'user_projects', 'ip_address'])
    @append_keyword_filter(_KEYWORD_FILTER)
    def list(self, params):
        """
        Args:
            params (dict): {
                    'cloud_service_id': 'str',
                    'name': 'str',
                    'state': 'str',
                    'account': 'str',
                    'instance_type': 'str',
                    'cloud_service_type': 'str',
                    'cloud_service_group': 'str',
                    'provider': 'str',
                    'region_code': 'str',
                    'resource_group_id': 'str',
                    'project_id': 'str',
                    'project_group_id': 'str',
                    'domain_id': 'str',
                    'query': 'dict (spaceone.api.core.v1.Query)',
                    'user_projects': 'list', // from meta
                    'ip_address': 'str'
                }

        Returns:
            results (list)
            total_count (int)

        """

        query = params.get('query', {})
        query = self._append_resource_group_filter(query, params['domain_id'])
        query = self._change_project_group_filter(query, params['domain_id'])
        query = self._change_tags_filter(query)
        query = self._change_only_tags(query)
        query = self._change_sort_tags(query)

        return self.cloud_svc_mgr.list_cloud_services(query)

    @transaction(append_meta={'authorization.scope': 'PROJECT'})
    @check_required(['query', 'domain_id'])
    @append_query_filter(['resource_group_id', 'domain_id', 'user_projects'])
    @append_keyword_filter(_KEYWORD_FILTER)
    def stat(self, params):
        """
        Args:
            params (dict): {
                'resource_group_id': 'str',
                'domain_id': 'str',
                'query': 'dict (spaceone.api.core.v1.StatisticsQuery)',
                'user_projects': 'list', // from meta
            }

        Returns:
            values (list) : 'list of statistics data'

        """

        query = params.get('query', {})
        query = self._append_resource_group_filter(query, params['domain_id'])
        query = self._change_project_group_filter(query, params['domain_id'])

        return self.cloud_svc_mgr.stat_cloud_services(query)

    def _append_resource_group_filter(self, query, domain_id):
        change_filter = []

        for condition in query.get('filter', []):
            key = condition.get('k', condition.get('key'))
            value = condition.get('v', condition.get('value'))
            operator = condition.get('o', condition.get('operator'))

            if key == 'resource_group_id':
                cloud_service_ids = None

                if operator in ['not', 'not_contain', 'not_in', 'not_contain_in']:
                    resource_group_operator = 'not_in'
                else:
                    resource_group_operator = 'in'

                if operator in ['eq', 'not', 'contain', 'not_contain']:
                    cloud_service_ids = self._get_cloud_service_ids_from_resource_group_id(value, domain_id)
                elif operator in ['in', 'not_in', 'contain_in', 'not_contain_in'] and isinstance(value, list):
                    cloud_service_ids = []
                    for v in value:
                        cloud_service_ids += self._get_cloud_service_ids_from_resource_group_id(v, domain_id)

                if cloud_service_ids is not None:
                    change_filter.append({
                        'k': 'cloud_service_id',
                        'v': list(set(cloud_service_ids)),
                        'o': resource_group_operator
                    })

            else:
                change_filter.append(condition)

        query['filter'] = change_filter
        return query

    def _get_cloud_service_ids_from_resource_group_id(self, resource_group_id, domain_id):
        resource_type = 'inventory.CloudService'
        rg_mgr: ResourceGroupManager = self.locator.get_manager('ResourceGroupManager')

        resource_group_filters = rg_mgr.get_resource_group_filter(resource_group_id, resource_type, domain_id,
                                                                  _KEYWORD_FILTER)
        cloud_service_ids = []
        for resource_group_query in resource_group_filters:
            resource_group_query['distinct'] = 'cloud_service_id'
            result = self.cloud_svc_mgr.stat_cloud_services(resource_group_query)
            cloud_service_ids += result.get('results', [])
        return cloud_service_ids

    def _change_project_group_filter(self, query, domain_id):
        change_filter = []

        project_group_query = {
            'filter': [],
            'only': ['project_group_id']
        }

        for condition in query.get('filter', []):
            key = condition.get('key', condition.get('k'))
            value = condition.get('value', condition.get('v'))
            operator = condition.get('operator', condition.get('o'))

            if not all([key, operator]):
                raise ERROR_DB_QUERY(reason='filter condition should have key, value and operator.')

            if key == 'project_group_id':
                project_group_query['filter'].append(condition)
            else:
                change_filter.append(condition)

        if len(project_group_query['filter']) > 0:
            identity_mgr: IdentityManager = self.locator.get_manager('IdentityManager')
            response = identity_mgr.list_project_groups(project_group_query, domain_id)
            project_group_ids = []
            project_ids = []
            for project_group_info in response.get('results', []):
                project_group_ids.append(project_group_info['project_group_id'])

            for project_group_id in project_group_ids:
                response = identity_mgr.list_projects_in_project_group(project_group_id, domain_id, True,
                                                                       {'only': ['project_id']})
                for project_info in response.get('results', []):
                    if project_info['project_id'] not in project_ids:
                        project_ids.append(project_info['project_id'])

            change_filter.append({'k': 'project_id', 'v': project_ids, 'o': 'in'})

        query['filter'] = change_filter
        return query

    @staticmethod
    def _change_only_tags(query):
        change_only_tags = []
        if 'only' in query:

            for key in query.get('only', []):
                if key.startswith('tags.'):
                    change_only_tags.append('tags')
                else:
                    change_only_tags.append(key)
            query['only'] = change_only_tags
        return query

    @staticmethod
    def _make_cloud_service_type_key(resource_data):
        return f'{resource_data["domain_id"]}.{resource_data["provider"]}.' \
               f'{resource_data["cloud_service_group"]}.{resource_data["cloud_service_type"]}'

    @staticmethod
    def _make_region_key(resource_data, provider):
        return f'{resource_data["domain_id"]}.{provider}.{resource_data["region_code"]}'

    def _change_metadata_path(self, metadata, provider=None):
        if provider is None:
            provider = 'custom'

        plugin_id = self.transaction.get_meta('plugin_id')

        if plugin_id:
            metadata = {
                plugin_id: copy.deepcopy(metadata)
            }
        else:
            metadata = {
                'MANUAL': copy.deepcopy(metadata)
            }

        return {provider: metadata}

    def _get_collection_info(self, provider=None, collections: CollectionInfo = None):
        if provider is None:
            provider = 'custom'

        if collections is None:
            collections = []

        collector_id = self.transaction.get_meta('collector_id')
        secret_id = self.transaction.get_meta('secret.secret_id')
        service_account_id = self.transaction.get_meta('secret.service_account_id')

        new_collection = {
            'provider': provider,
            'collector_id': collector_id,
            'secret_id': secret_id,
            'service_account_id': service_account_id,
            'last_collected_at': datetime.utcnow()
        }

        collections.append(new_collection)
        return collections

    @staticmethod
    def _merge_collection_info(old_collection_info, new_collection_info):
        merged_collection_info = []
        for old_collection in old_collection_info:
            last_collected_at = old_collection.get('last_collected_at')
            del old_collection['last_collected_at']

            for new_collection in new_collection_info:
                del new_collection['last_collected_at']

                if old_collection == new_collection:
                    old_collection.update({'last_collected_at': last_collected_at})
                    merged_collection_info.append(old_collection)

        for new_collection in new_collection_info:
            if new_collection not in merged_collection_info:
                merged_collection_info.append(new_collection)

        return merged_collection_info

    @staticmethod
    def _create_tags(tags: dict, provider=None):
        if provider is None:
            provider = 'custom'

        if isinstance(tags, list):
            dot_tags = utils.tags_to_dict(tags)

        elif isinstance(tags, dict):
            dot_tags = copy.deepcopy(tags)
        else:
            dot_tags = {}

        tags = {provider: {}}
        for key, value in dot_tags.items():
            tags[provider].update({utils.string_to_hash(key): {'key': key, 'value': value}})
        return tags

    @staticmethod
    def _create_tag_keys(tags: dict, provider=None):
        if provider is None:
            provider = 'custom'
        return {provider: list(tags.keys())}

    def _get_provider_from_meta(self):
        if self.collector_id and self.job_id and self.service_account_id and self.plugin_id:
            provider = self.transaction.get_meta(self.transaction.get_meta('secret.provider'))
        else:
            provider = 'custom'
        return provider

    @staticmethod
    def _change_tags_filter(query):
        change_filter = []

        for condition in query.get('filter', []):
            key = condition.get('k', condition.get('key'))
            value = condition.get('v', condition.get('value'))
            operator = condition.get('o', condition.get('operator'))

            if key.startswith('tags.'):
                prefix, provider, key = key.split('.', 2)
                hash_value = utils.string_to_hash(key)
                key = f'{prefix}.{provider}.{hash_value}.value'

                change_filter.append({
                    'key': key,
                    'value': value,
                    'operator': operator
                })

            else:
                change_filter.append(condition)
        query['filter'] = change_filter
        return query

    @staticmethod
    def _change_sort_tags(query):
        change_filter = []
        if 'sort' in query:
            sort_keys = query['sort'].get('keys', [])
            for condition in sort_keys:
                sort_key = condition.get('key', '')
                desc = condition.get('desc', False)

                if sort_key.startswith('tags.'):
                    prefix, provider, key = sort_key.split('.', 2)
                    hash_value = utils.string_to_hash(key)
                    key = f'{prefix}.{provider}.{hash_value}.value'

                    change_filter.append({
                        'key': key,
                        'desc': desc
                    })

                else:
                    change_filter.append({
                        'key': sort_key,
                        'desc': desc
                    })

            query['sort']['keys'] = change_filter
        return query
