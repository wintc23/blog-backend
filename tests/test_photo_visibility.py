"""Visitor privacy and independent, inherited album membership visibility."""
import json
import unittest
import test_albums as fixture
from app import db
from app.models import PersonalProfile


class PhotoVisibilityTests(unittest.TestCase):
    owner = fixture.AlbumsTests.owner
    regular = fixture.AlbumsTests.regular
    png = fixture.AlbumsTests.png
    cloud_upload = fixture.AlbumsTests.cloud_upload
    create = fixture.AlbumsTests.create
    upload = fixture.AlbumsTests.upload
    album = fixture.AlbumsTests.album
    add = fixture.AlbumsTests.add

    def setUp(self):
        fixture.AlbumsTests.setUp(self)
        PersonalProfile.__table__.create(db.engine)
        self.hidden = 'https://example.test/portrait.jpg'
        self.visible = 'https://example.test/landscape.jpg'
        self.data = {'text': '两张照片', 'date': '2026-09-19', 'category': 'daily', 'images': [
            {'url': self.hidden, 'description': '隐藏人像', 'is_public': False},
            {'url': self.visible, 'description': '风景'}]}
        response = self.client.post('/api/life-moments/', json=self.data, headers=self.headers)
        self.assertEqual(response.status_code, 201)
        self.moment = response.get_json()

    def tearDown(self): fixture.AlbumsTests.tearDown(self)

    def test_public_list_detail_groups_cover_and_legacy_profile_never_leak(self):
        db.session.add(PersonalProfile(id=1, moments_json=json.dumps([dict(self.moment, image_url=self.hidden)])))
        db.session.commit()
        paths = ['/api/life-moments/', '/api/life-moments/?group_by=date', '/api/life-moments/'+self.moment['id']+'/', '/api/personal-profile/']
        for path in paths:
            for headers in ({}, self.viewer_headers):
                response = self.client.get(path, headers=headers)
                self.assertEqual(response.status_code, 200)
                self.assertNotIn(self.hidden, response.get_data(as_text=True))
                self.assertNotIn('隐藏人像', response.get_data(as_text=True))
                self.assertIn('no-store', response.headers['Cache-Control'])
        response = self.client.get(paths[2], headers=self.headers)
        self.assertEqual(len(response.get_json()['images']), 2)
        self.assertEqual(response.get_json()['images'][0]['is_public'], False)
        self.client.delete(paths[2], headers=self.headers)
        self.assertNotIn(self.hidden, self.client.get('/api/personal-profile/').get_data(as_text=True))

    def test_visibility_authorization_validation_and_all_hidden(self):
        path = '/api/life-moments/'+self.moment['id']+'/images/visibility/'
        change = dict(index=1, url=self.visible, is_public=False)
        self.assertEqual(self.client.patch(path, json=change).status_code, 401)
        self.assertEqual(self.client.patch(path, json=change, headers=self.viewer_headers).status_code, 403)
        self.assertEqual(self.client.patch(path, json=dict(change, url=self.hidden), headers=self.headers).status_code, 400)
        self.assertEqual(self.client.patch(path, json=dict(change, is_public='false'), headers=self.headers).status_code, 400)
        self.assertEqual(self.client.patch(path, json=change, headers=self.headers).status_code, 200)
        data = self.client.get('/api/life-moments/'+self.moment['id']+'/').get_json()
        self.assertEqual(data['images'], [])
        self.assertEqual(data['image_url'], '')
        self.assertEqual(data['image_alt'], '')
        self.assertEqual(self.client.patch(path, json=dict(change, is_public=True), headers=self.headers).status_code, 200)
        self.assertEqual(len(self.client.get('/api/life-moments/'+self.moment['id']+'/').get_json()['images']), 1)

    def test_albums_inherit_once_then_remain_independent_and_revoke_tickets(self):
        first, second = self.album('public'), self.album('public')
        source = {'type': 'moment', 'id': self.moment['id'], 'index': 0}
        first = self.add(first, source); second = self.add(second, source)
        photo = first['photos'][0]
        for album in (first, second):
            self.assertFalse(album['photos'][0]['is_public'])
            value = self.client.get('/api/albums/'+album['id']+'/').get_json()
            self.assertEqual((value['photos'], value['count'], value['cover']), ([], 0, None))
        path = '/api/albums/{}/photos/{}/'.format(first['id'], photo['id'])
        self.assertEqual(self.client.patch(path, json={'is_public': True}, headers=self.viewer_headers).status_code, 403)
        self.assertEqual(self.client.patch(path, json={'is_public': True}, headers=self.headers).status_code, 200)
        visible = self.client.get('/api/albums/'+first['id']+'/').get_json()
        public_url = '/api'+visible['photos'][0]['url']
        self.assertEqual(self.client.get(public_url).status_code, 302)
        self.assertEqual(self.client.get('/api/albums/'+second['id']+'/').get_json()['count'], 0)
        self.assertEqual(len(self.client.get('/api/life-moments/'+self.moment['id']+'/').get_json()['images']), 1)
        self.client.patch('/api/life-moments/'+self.moment['id']+'/images/visibility/', json=dict(index=0, url=self.hidden, is_public=True), headers=self.headers)
        # Changing the source does not unhide existing album memberships.
        self.assertEqual(self.client.get('/api/albums/'+second['id']+'/').get_json()['count'], 0)
        self.add(second, source)
        self.assertEqual(self.client.get('/api/albums/'+second['id']+'/').get_json()['count'], 0)
        self.client.patch(path, json={'is_public': False}, headers=self.headers)
        self.assertEqual(self.client.get(public_url).status_code, 403)
        self.assertEqual(self.client.get('/api/albums/'+first['id']+'/').get_json()['cover'], None)
