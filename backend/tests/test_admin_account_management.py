"""Integration checks for the ADMIN user directory (needs test PostgreSQL)."""
from __future__ import annotations

import uuid

import pytest

from services import auth as auth_service
from utils.security import Role

pytestmark = pytest.mark.postgres


def email(prefix):
    return f"{prefix}-{uuid.uuid4().hex[:10]}@example.com"


def test_admin_can_manage_accounts_without_erasing_audit_history(pg_app, pg_client):
    with pg_app.app_context():
        admin = auth_service.create_user(
            email=email('admin'), password='Admin2026#Strong',
            full_name='Test Administrator', role=Role.ADMIN.value,
        )
        user = auth_service.create_user(
            email=email('valet'), password='Valet2026#Strong',
            full_name='Original Valet', role=Role.VALET_OPERATOR.value, actor=admin,
        )
        admin_email, user_id = admin.email, str(user.id)
        other_email = email('updated')

    auth = pg_client.post('/api/v1/auth/login', json={
        'email': admin_email, 'password': 'Admin2026#Strong',
    })
    assert auth.status_code == 200
    token = auth.get_json()['data']['access_token']
    headers = {'Authorization': f'Bearer {token}'}

    directory = pg_client.get('/api/v1/auth/users', headers=headers)
    assert directory.status_code == 200
    data = directory.get_json()['data']
    assert any(item['id'] == user_id for item in data['users'])
    assert all('password' not in item for item in data['users'])

    edit = pg_client.patch(f'/api/v1/auth/users/{user_id}', headers=headers, json={
        'full_name': 'Updated Valet', 'email': other_email, 'role': Role.VALET_MANAGER.value,
    })
    assert edit.status_code == 200
    assert edit.get_json()['data']['user']['role'] == Role.VALET_MANAGER.value
    assert edit.get_json()['data']['user']['email'] == other_email

    reset = pg_client.post(f'/api/v1/auth/users/{user_id}/password', headers=headers, json={
        'password': 'NewValet2026#Secure',
    })
    assert reset.status_code == 200
    assert 'password_hash' not in reset.get_json()['data']['user']
    login = pg_client.post('/api/v1/auth/login', json={
        'email': other_email, 'password': 'NewValet2026#Secure',
    })
    assert login.status_code == 200

    delete = pg_client.delete(f'/api/v1/auth/users/{user_id}', headers=headers)
    assert delete.status_code == 200
    assert delete.get_json()['data']['user']['status'] == 'DEACTIVATED'
    assert pg_client.post('/api/v1/auth/login', json={
        'email': other_email, 'password': 'NewValet2026#Secure',
    }).status_code == 401
    restore = pg_client.post(f'/api/v1/auth/users/{user_id}/reactivate', headers=headers)
    assert restore.status_code == 200
    assert restore.get_json()['data']['user']['status'] == 'ACTIVE'


def test_admin_accounts_protected_and_nonadmin_forbidden(pg_app, pg_client):
    with pg_app.app_context():
        admin = auth_service.create_user(
            email=email('admin'), password='Admin2026#Strong',
            full_name='Protected Admin', role=Role.ADMIN.value,
        )
        valet = auth_service.create_user(
            email=email('valet'), password='Valet2026#Strong',
            full_name='Non-admin', role=Role.VALET_OPERATOR.value,
            actor=admin,
        )
        admin_id, admin_email, valet_email = str(admin.id), admin.email, valet.email
    admin_response = pg_client.post('/api/v1/auth/login', json={
        'email': admin_email, 'password': 'Admin2026#Strong',
    })
    admin_headers = {'Authorization': f"Bearer {admin_response.get_json()['data']['access_token']}"}
    valet_response = pg_client.post('/api/v1/auth/login', json={
        'email': valet_email, 'password': 'Valet2026#Strong',
    })
    valet_headers = {'Authorization': f"Bearer {valet_response.get_json()['data']['access_token']}"}
    assert pg_client.get('/api/v1/auth/users', headers=valet_headers).status_code == 403
    assert pg_client.patch(f'/api/v1/auth/users/{admin_id}', headers=admin_headers,
                           json={'full_name': 'Changed Name'}).status_code == 403
    assert pg_client.delete(f'/api/v1/auth/users/{admin_id}', headers=admin_headers).status_code == 403
    assert pg_client.patch(f'/api/v1/auth/users/{admin_id}', headers=valet_headers,
                           json={'role': 'ADMIN'}).status_code == 403
