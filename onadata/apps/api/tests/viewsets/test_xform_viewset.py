# coding: utf-8
# from __future__ import unicode_literals, absolute_import  - Tests failed when imported

import json
import os
import re
import unittest
from datetime import datetime
from xml.dom import minidom, Node

import pytz
import requests
from django.conf import settings
from django.utils import timezone
from guardian.shortcuts import assign_perm
from httmock import urlmatch, HTTMock, all_requests
from rest_framework import status

from onadata.apps.api.tests.viewsets.test_abstract_viewset import \
    TestAbstractViewSet
from onadata.apps.api.viewsets.xform_viewset import XFormViewSet
from onadata.apps.logger.models import XForm
from onadata.apps.main.models import MetaData
from onadata.libs.permissions import (
    CAN_VIEW_XFORM
)
from onadata.libs.serializers.xform_serializer import XFormSerializer


@all_requests
def enketo_mock(url, request):
    response = requests.Response()
    response.status_code = 201
    response._content = \
        '{\n  "url": "https:\\/\\/dmfrm.enketo.org\\/webform",\n'\
        '  "code": "200"\n}'
    return response


@all_requests
def enketo_error_mock(url, request):
    response = requests.Response()
    response.status_code = 400
    response._content = \
        '{\n  "message": "no account exists for this OpenRosa server",\n'\
        '  "code": "200"\n}'
    return response


@all_requests
def external_mock(url, request):
    response = requests.Response()
    response.status_code = 201
    response._content = '/xls/ee3ff9d8f5184fc4a8fdebc2547cc059'
    return response


class TestXFormViewSet(TestAbstractViewSet):

    def setUp(self):
        super(self.__class__, self).setUp()
        self.view = XFormViewSet.as_view({
            'get': 'list',
        })

    def test_form_list(self):
        request = self.factory.get('/', **self.extra)
        response = self.view(request)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_submission_count_for_today_in_form_list(self):

        self.publish_xls_form()

        request = self.factory.get('/', **self.extra)
        response = self.view(request)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('submission_count_for_today', response.data[0].keys())
        self.assertEqual(response.data[0]['submission_count_for_today'], 0)
        self.assertEqual(response.data[0]['num_of_submissions'], 0)

        paths = [os.path.join(
            self.main_directory, 'fixtures', 'transportation',
            'instances_w_uuid', s, s + '.xml')
            for s in ['transport_2011-07-25_19-05-36']]

        # instantiate date that is NOT naive; timezone is enabled
        current_timezone_name = timezone.get_current_timezone_name()
        current_timezone = pytz.timezone(current_timezone_name)
        today = datetime.today()
        current_date = current_timezone.localize(
            datetime(today.year,
                     today.month,
                     today.day))
        self._make_submission(paths[0], forced_submission_time=current_date)
        self.assertEqual(self.response.status_code, status.HTTP_201_CREATED)

        request = self.factory.get('/', **self.extra)
        response = self.view(request)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data[0]['submission_count_for_today'], 1)
        self.assertEqual(response.data[0]['num_of_submissions'], 1)

    def test_form_list_anon(self):
        self.publish_xls_form()
        request = self.factory.get('/')
        response = self.view(request)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, [])

    def test_public_form_list(self):
        self.publish_xls_form()

        self.view = XFormViewSet.as_view({
            'get': 'retrieve',
        })
        request = self.factory.get('/', **self.extra)
        response = self.view(request, pk='public')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, [])

        # public shared form
        self.xform.shared = True
        self.xform.save()
        response = self.view(request, pk='public')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.form_data['public'] = True
        del self.form_data['date_modified']
        del response.data[0]['date_modified']
        self.assertEqual(response.data, [self.form_data])

        # public shared form data
        self.xform.shared_data = True
        self.xform.shared = False
        self.xform.save()
        response = self.view(request, pk='public')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, [])

    def test_form_list_other_user_access(self):
        """
        Test that a different user has no access to bob's form
        """
        self.publish_xls_form()

        request = self.factory.get('/', **self.extra)
        response = self.view(request)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, [self.form_data])

        # test with different user
        previous_user = self.user
        alice_data = {'username': 'alice', 'email': 'alice@localhost.com'}
        self._login_user_and_profile(extra_post_data=alice_data)
        self.assertEqual(self.user.username, 'alice')
        self.assertNotEqual(previous_user,  self.user)
        request = self.factory.get('/', **self.extra)
        response = self.view(request)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # should be empty
        self.assertEqual(response.data, [])

    def test_form_list_filter_by_user(self):
        # publish bob's form
        self.publish_xls_form()

        previous_user = self.user
        alice_data = {'username': 'alice', 'email': 'alice@localhost.com'}
        self._login_user_and_profile(extra_post_data=alice_data)
        self.assertEqual(self.user.username, 'alice')
        self.assertNotEqual(previous_user,  self.user)

        assign_perm(CAN_VIEW_XFORM, self.user, self.xform)
        view = XFormViewSet.as_view({
            'get': 'retrieve'
        })
        request = self.factory.get('/', **self.extra)
        response = view(request, pk=self.xform.pk)
        bobs_form_data = response.data

        # publish alice's form
        self.publish_xls_form()

        request = self.factory.get('/', **self.extra)
        response = self.view(request)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # should be both bob's and alice's form
        self.assertEqual(sorted(response.data),
                         sorted([bobs_form_data, self.form_data]))

        # apply filter, see only bob's forms
        request = self.factory.get('/', data={'owner': 'bob'}, **self.extra)
        response = self.view(request)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, [bobs_form_data])

        # apply filter, see only alice's forms
        request = self.factory.get('/', data={'owner': 'alice'}, **self.extra)
        response = self.view(request)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, [self.form_data])

        # apply filter, see a non existent user
        request = self.factory.get('/', data={'owner': 'noone'}, **self.extra)
        response = self.view(request)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, [])

    def test_form_get(self):
        self.publish_xls_form()
        view = XFormViewSet.as_view({
            'get': 'retrieve'
        })
        formid = self.xform.pk
        request = self.factory.get('/', **self.extra)
        response = view(request, pk=formid)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, self.form_data)

    def test_form_format(self):
        self.publish_xls_form()
        view = XFormViewSet.as_view({
            'get': 'form'
        })
        formid = self.xform.pk
        data = {
            "name": "transportation_2011_07_25",  # Since commit 3c0e17d0b6041ae96b06c3ef4d2f78a2d0739cbc
            "title": "transportation_2011_07_25",
            "default_language": "default",
            "id_string": "transportation_2011_07_25",
            "type": "survey",
        }
        request = self.factory.get('/', **self.extra)
        # test for unsupported format
        response = view(request, pk=formid, format='csvzip')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

        # test for supported formats
        response = view(request, pk=formid, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertDictContainsSubset(data, response.data)
        response = view(request, pk=formid, format='xml')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        response_doc = minidom.parseString(response.data)
        response = view(request, pk=formid, format='xls')
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        xml_path = os.path.join(
            settings.ONADATA_DIR, "apps", "main", "tests", "fixtures",
            "transportation", "transportation.xml")
        with open(xml_path) as xml_file:
            expected_doc = minidom.parse(xml_file)

        model_node = [
            n for n in
            response_doc.getElementsByTagName("h:head")[0].childNodes
            if n.nodeType == Node.ELEMENT_NODE and
            n.tagName == "model"][0]

        # check for UUID and remove
        uuid_nodes = [
            node for node in model_node.childNodes
            if node.nodeType == Node.ELEMENT_NODE
            and node.getAttribute("nodeset") == "/transportation_2011_07_25/formhub/uuid"]
        self.assertEqual(len(uuid_nodes), 1)
        uuid_node = uuid_nodes[0]
        uuid_node.setAttribute("calculate", "''")

        # check content without UUID
        self.assertEqual(response_doc.toxml(), expected_doc.toxml())

    def test_form_tags(self):
        self.publish_xls_form()
        view = XFormViewSet.as_view({
            'get': 'labels',
            'post': 'labels',
            'delete': 'labels'
        })
        list_view = XFormViewSet.as_view({
            'get': 'list',
        })
        formid = self.xform.pk

        # no tags
        request = self.factory.get('/', **self.extra)
        response = view(request, pk=formid)
        self.assertEqual(response.data, [])

        # add tag "hello"
        request = self.factory.post('/', data={"tags": "hello"}, **self.extra)
        response = view(request, pk=formid)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data, [u'hello'])

        # check filter by tag
        request = self.factory.get('/', data={"tags": "hello"}, **self.extra)
        self.form_data = XFormSerializer(
            self.xform, context={'request': request}).data
        response = list_view(request, pk=formid)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, [self.form_data])

        request = self.factory.get('/', data={"tags": "goodbye"}, **self.extra)
        response = list_view(request, pk=formid)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, [])

        # remove tag "hello"
        request = self.factory.delete('/', data={"tags": "hello"},
                                      **self.extra)
        response = view(request, pk=formid, label='hello')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, [])

    def test_enketo_url_no_account(self):
        self.publish_xls_form()
        view = XFormViewSet.as_view({
            'get': 'enketo'
        })
        formid = self.xform.pk
        # no tags
        request = self.factory.get('/', **self.extra)
        with HTTMock(enketo_error_mock):
            response = view(request, pk=formid)
            data = {'message': u"Enketo not properly configured."}

            self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
            self.assertEqual(response.data, data)

    def test_enketo_url(self):
        self.publish_xls_form()
        view = XFormViewSet.as_view({
            'get': 'enketo'
        })
        formid = self.xform.pk
        # no tags
        request = self.factory.get('/', **self.extra)
        with HTTMock(enketo_mock):
            response = view(request, pk=formid)
            data = {"enketo_url": "https://dmfrm.enketo.org/webform"}
            self.assertEqual(response.data, data)

    def test_publish_xlsform(self):
        view = XFormViewSet.as_view({
            'post': 'create'
        })
        data = {
            'owner': 'http://testserver/api/v1/users/bob',
            'public': False,
            'public_data': False,
            'description': u'transportation_2011_07_25',
            'downloadable': True,
            'allows_sms': False,
            'encrypted': False,
            'sms_id_string': u'transportation_2011_07_25',
            'id_string': u'transportation_2011_07_25',
            'title': u'transportation_2011_07_25',
            'bamboo_dataset': u''
        }
        path = os.path.join(
            settings.ONADATA_DIR, "apps", "main", "tests", "fixtures",
            "transportation", "transportation.xls")
        with open(path) as xls_file:
            post_data = {'xls_file': xls_file}
            request = self.factory.post('/', data=post_data, **self.extra)
            response = view(request)
            self.assertEqual(response.status_code, status.HTTP_201_CREATED)
            xform = self.user.xforms.get(uuid=response.data.get('uuid'))
            data.update({
                'url':
                'http://testserver/api/v1/forms/%s' % xform.pk
            })
            self.assertDictContainsSubset(data, response.data)
            self.assertTrue(xform.user.pk == self.user.pk)

    def test_publish_invalid_xls_form(self):
        view = XFormViewSet.as_view({
            'post': 'create'
        })
        path = os.path.join(
            settings.ONADATA_DIR, "apps", "main", "tests", "fixtures",
            "transportation", "transportation.bad_id.xls")
        with open(path) as xls_file:
            post_data = {'xls_file': xls_file}
            request = self.factory.post('/', data=post_data, **self.extra)
            response = view(request)
            self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
            error_msg = '[row : 5] Question or group with no name.'
            self.assertEqual(response.data.get('text'), error_msg)

    def test_publish_invalid_xls_form_no_choices(self):
        view = XFormViewSet.as_view({
            'post': 'create'
        })
        path = os.path.join(
            settings.ONADATA_DIR, "apps", "main", "tests", "fixtures",
            "transportation", "transportation.no_choices.xls")
        with open(path) as xls_file:
            post_data = {'xls_file': xls_file}
            request = self.factory.post('/', data=post_data, **self.extra)
            response = view(request)
            self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
            error_msg = ("There should be a choices sheet in this xlsform. "
                         "Please ensure that the choices sheet name is all in "
                         "small caps and has columns 'list name', 'name', "
                         "and 'label' (or aliased column names).")
            self.assertEqual(response.data.get('text'), error_msg)

    def test_partial_update(self):
        self.publish_xls_form()
        view = XFormViewSet.as_view({
            'patch': 'partial_update'
        })
        title = u'مرحب'
        description = 'DESCRIPTION'
        data = {'public': True, 'description': description, 'title': title,
                'downloadable': True}

        self.assertFalse(self.xform.shared)

        request = self.factory.patch('/', data=data, **self.extra)
        response = view(request, pk=self.xform.id)

        self.xform.reload()
        self.assertTrue(self.xform.downloadable)
        self.assertTrue(self.xform.shared)
        self.assertEqual(self.xform.description, description)
        self.assertEqual(response.data['public'], True)
        self.assertEqual(response.data['description'], description)
        self.assertEqual(response.data['title'], title)
        matches = re.findall(r"<h:title>([^<]+)</h:title>", self.xform.xml)
        self.assertTrue(len(matches) > 0)
        self.assertEqual(matches[0], title)

    def test_set_form_private(self):
        key = 'shared'
        self.publish_xls_form()
        self.xform.__setattr__(key, True)
        self.xform.save()
        view = XFormViewSet.as_view({
            'patch': 'partial_update'
        })
        data = {'public': False}

        self.assertTrue(self.xform.__getattribute__(key))

        request = self.factory.patch('/', data=data, **self.extra)
        response = view(request, pk=self.xform.id)
        self.xform.refresh_from_db()
        self.assertFalse(self.xform.__getattribute__(key))
        self.assertFalse(response.data['public'])

    def test_set_form_bad_value(self):
        key = 'shared'
        self.publish_xls_form()
        view = XFormViewSet.as_view({
            'patch': 'partial_update'
        })
        data = {'public': 'String'}

        request = self.factory.patch('/', data=data, **self.extra)
        response = view(request, pk=self.xform.id)

        self.xform.reload()
        self.assertFalse(self.xform.__getattribute__(key))
        self.assertEqual(response.data,
                         {'shared':
                          [u"'String' value must be either True or False."]})

    def test_set_form_bad_key(self):
        self.publish_xls_form()
        self.xform.save()
        view = XFormViewSet.as_view({
            'patch': 'partial_update'
        })
        data = {'nonExistentField': False}

        request = self.factory.patch('/', data=data, **self.extra)
        response = view(request, pk=self.xform.id)

        self.xform.reload()
        self.assertFalse(self.xform.shared)
        self.assertFalse(response.data['public'])

    def test_form_delete(self):
        self.publish_xls_form()
        self.xform.save()
        view = XFormViewSet.as_view({
            'delete': 'destroy'
        })
        formid = self.xform.pk
        request = self.factory.delete('/', **self.extra)
        response = view(request, pk=formid)
        self.assertEqual(response.data, None)
        self.assertEqual(response.status_code, 204)
        with self.assertRaises(XForm.DoesNotExist):
            self.xform.reload()

    def test_xform_serializer_none(self):
        data = {
            'title': u'',
            'owner': None,
            'public': False,
            'public_data': False,
            'require_auth': False,
            'description': u'',
            'downloadable': False,
            'allows_sms': False,
            'uuid': u'',
            'instances_with_geopoints': False,
            'num_of_submissions': 0,
            'has_kpi_hooks': False,
            'kpi_asset_uid': u'',
        }
        self.assertEqual(data, XFormSerializer(None).data)

    def test_external_export(self):
        self.publish_xls_form()

        data_value = 'template 1|http://xls_server'
        self._add_form_metadata(self.xform, 'external_export',
                                data_value)
        metadata = MetaData.objects.get(xform=self.xform,
                                        data_type='external_export')
        paths = [os.path.join(
            self.main_directory, 'fixtures', 'transportation',
            'instances_w_uuid', s, s + '.xml')
            for s in ['transport_2011-07-25_19-05-36']]

        self._make_submission(paths[0])
        self.assertEqual(self.response.status_code, status.HTTP_201_CREATED)

        view = XFormViewSet.as_view({
            'get': 'retrieve',
        })
        data = {'meta': metadata.pk}
        formid = self.xform.pk
        request = self.factory.get('/', data=data, **self.extra)

        with HTTMock(external_mock):
            # External export
            response = view(request, pk=formid, format='xls')
            self.assertEqual(response.status_code, status.HTTP_302_FOUND)
            expected_url = 'http://xls_server/xls/ee3ff9d8f5184fc4a8fdebc2547cc059'
            self.assertEquals(response.url, expected_url)

    def test_external_export_error(self):
        self.publish_xls_form()

        data_value = 'template 1|http://xls_server'
        self._add_form_metadata(self.xform, 'external_export',
                                data_value)

        paths = [os.path.join(
            self.main_directory, 'fixtures', 'transportation',
            'instances_w_uuid', s, s + '.xml')
            for s in ['transport_2011-07-25_19-05-36']]

        self._make_submission(paths[0])
        self.assertEqual(self.response.status_code, status.HTTP_201_CREATED)

        view = XFormViewSet.as_view({
            'get': 'retrieve',
        })
        formid = self.xform.pk
        token = 'http://xls_server/xls/' +\
            '8e86d4bdfa7f435ab89485aeae4ea6f5'
        data = {'token': token}
        request = self.factory.get('/', data=data, **self.extra)

        # External export
        response = view(
            request,
            pk=formid,
            format='xls')

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        data = json.loads(response.data)
        self.assertTrue(data.get('error')
                        .startswith("J2X client could not generate report."))

    def test_csv_import(self):
        self.publish_xls_form()
        view = XFormViewSet.as_view({'post': 'csv_import'})
        csv_import = open(os.path.join(settings.ONADATA_DIR, 'libs',
                                       'tests', 'fixtures', 'good.csv'))
        post_data = {'csv_file': csv_import}
        request = self.factory.post('/', data=post_data, **self.extra)
        response = view(request, pk=self.xform.id)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data.get('additions'), 9)
        self.assertEqual(response.data.get('updates'), 0)

    def test_csv_import_fail(self):
        self.publish_xls_form()
        view = XFormViewSet.as_view({'post': 'csv_import'})
        csv_import = open(os.path.join(settings.ONADATA_DIR, 'libs',
                                       'tests', 'fixtures', 'bad.csv'))
        post_data = {'csv_file': csv_import}
        request = self.factory.post('/', data=post_data, **self.extra)
        response = view(request, pk=self.xform.id)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIsNotNone(response.data.get('error'))

    def test_csv_import_fail_invalid_field_post(self):
        """
        Test that invalid post returns 400 with the error in json response
        """
        self.publish_xls_form()
        view = XFormViewSet.as_view({'post': 'csv_import'})
        csv_import = open(os.path.join(settings.ONADATA_DIR, 'libs',
                                       'tests', 'fixtures', 'bad.csv'))
        post_data = {'wrong_file_field': csv_import}
        request = self.factory.post('/', data=post_data, **self.extra)
        response = view(request, pk=self.xform.id)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIsNotNone(response.data.get('error'))
