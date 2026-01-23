"""
Tests for the `EnterpriseCustomerAdminViewSet`.
"""
import ddt
from edx_rbac.constants import ALL_ACCESS_CONTEXT
from rest_framework import status
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import AccessToken

from django.urls import reverse

from enterprise.constants import (
    ENTERPRISE_ADMIN_ROLE,
    ENTERPRISE_LEARNER_ROLE,
    ENTERPRISE_OPERATOR_ROLE,
    SYSTEM_ENTERPRISE_CATALOG_ADMIN_ROLE,
    SYSTEM_ENTERPRISE_PROVISIONING_ADMIN_ROLE,
)
from enterprise.models import EnterpriseCustomerAdmin, SystemWideEnterpriseUserRoleAssignment
from test_utils import APITest
from test_utils.factories import (
    EnterpriseCustomerFactory,
    EnterpriseCustomerUserFactory,
    OnboardingFlowFactory,
    UserFactory,
)


class TestEnterpriseCustomerAdminViewSet(APITestCase):
    """
    Tests for EnterpriseCustomerAdminViewSet.
    """
    def setUp(self):
        """Set up test data."""
        self.user = UserFactory()
        self.client.force_authenticate(user=self.user)

        self.enterprise_customer = EnterpriseCustomerFactory()
        self.enterprise_customer_user = EnterpriseCustomerUserFactory(
            user_id=self.user.id,
            enterprise_customer=self.enterprise_customer
        )

        self.admin = EnterpriseCustomerAdmin.objects.create(
            enterprise_customer_user=self.enterprise_customer_user
        )

        self.flow1 = OnboardingFlowFactory()
        self.flow2 = OnboardingFlowFactory()

        self.admin.completed_tour_flows.add(self.flow1)

        self.list_url = reverse('enterprise-customer-admin-list')
        self.detail_url = reverse('enterprise-customer-admin-detail', kwargs={'pk': self.admin.uuid})
        self.complete_tour_flow_url = reverse(
            'enterprise-customer-admin-complete-tour-flow',
            kwargs={'pk': self.admin.uuid}
        )
        self.create_admin_by_email_url = reverse('enterprise-customer-admin-create-admin-by-email')

    def test_get_list(self):
        """
        Test GET request to list endpoint.
        """
        response = self.client.get(self.list_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data['results']), 1)

        admin_data = response.data['results'][0]
        self.assertEqual(admin_data['uuid'], str(self.admin.uuid))
        self.assertEqual(admin_data['onboarding_tour_dismissed'], False)
        self.assertEqual(admin_data['onboarding_tour_completed'], False)
        self.assertEqual(len(admin_data['completed_tour_flows']), 1)
        self.assertEqual(str(admin_data['completed_tour_flows'][0]), str(self.flow1.uuid))

    def test_complete_tour_flow_success(self):
        """
        Test successful completion of a tour flow.
        """
        data = {'flow_uuid': str(self.flow2.uuid)}
        response = self.client.post(self.complete_tour_flow_url, data)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['status'], 'success')
        self.assertIn(self.flow2.title, response.data['message'])

        self.admin.refresh_from_db()
        self.assertEqual(self.admin.completed_tour_flows.count(), 2)
        self.assertIn(self.flow2, self.admin.completed_tour_flows.all())

    def test_complete_tour_flow_missing_uuid(self):
        """
        Test completing a tour flow with missing UUID.
        """
        response = self.client.post(self.complete_tour_flow_url, {})

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data['error'], 'flow_uuid is required')

    def test_unauthorized_access(self):
        """
        Test unauthorized access to endpoints.
        """
        other_user = UserFactory()
        self.client.force_authenticate(user=other_user)

        response = self.client.get(self.list_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data['results']), 0)

        response = self.client.get(self.detail_url)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

        data = {'flow_uuid': str(self.flow2.uuid)}
        response = self.client.post(self.complete_tour_flow_url, data)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_create_method_not_allowed(self):
        """
        Test that POST to endpoint is not allowed.
        """
        data = {
            'enterprise_customer_user': self.enterprise_customer_user.id,
            'onboarding_tour_dismissed': False,
            'onboarding_tour_completed': False
        }
        response = self.client.post(self.list_url, data)
        self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)
        self.assertEqual(response.data['detail'], 'Method "POST" not allowed.')

    def test_delete_method_not_allowed(self):
        """
        Test that DELETE to endpoint is not allowed.
        """
        response = self.client.delete(self.list_url)
        self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)
        self.assertEqual(response.data['detail'], 'Method "DELETE" not allowed.')


@ddt.ddt
class TestCreateAdminByEmailEndpoint(APITest):
    """
    Tests for the create_admin_by_email endpoint.
    """
    def setUp(self):
        """Set up test data."""
        super().setUp()
        self.target_user = UserFactory(email='target@example.com')
        self.enterprise_customer = EnterpriseCustomerFactory()

        self.create_admin_by_email_url = reverse('enterprise-customer-admin-create-admin-by-email')

    def test_create_admin_by_email_success_new_admin(self):
        """
        Test successful creation of a new admin record.
        """
        self.set_jwt_cookie(SYSTEM_ENTERPRISE_PROVISIONING_ADMIN_ROLE, ALL_ACCESS_CONTEXT)

        data = {
            'email': self.target_user.email,
            'enterprise_customer_uuid': str(self.enterprise_customer.uuid)
        }

        response = self.client.post(self.create_admin_by_email_url, data)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertIn('uuid', response.data)

        # Verify EnterpriseCustomerAdmin was created
        self.assertTrue(
            EnterpriseCustomerAdmin.objects.filter(
                enterprise_customer_user__user_fk=self.target_user,
                enterprise_customer_user__enterprise_customer=self.enterprise_customer
            ).exists()
        )

        # Verify SystemWideEnterpriseUserRoleAssignment was created
        role_assignment = SystemWideEnterpriseUserRoleAssignment.objects.filter(
            user=self.target_user,
            role__name=ENTERPRISE_ADMIN_ROLE,
            enterprise_customer=self.enterprise_customer
        )
        self.assertTrue(role_assignment.exists())

    def test_create_admin_by_email_success_existing_admin(self):
        """
        Test successful response when admin record already exists.
        """
        self.set_jwt_cookie(SYSTEM_ENTERPRISE_PROVISIONING_ADMIN_ROLE, ALL_ACCESS_CONTEXT)

        # Create existing admin record
        enterprise_customer_user = EnterpriseCustomerUserFactory(
            user_id=self.target_user.id,
            enterprise_customer=self.enterprise_customer
        )
        existing_admin = EnterpriseCustomerAdmin.objects.create(
            enterprise_customer_user=enterprise_customer_user
        )

        data = {
            'email': self.target_user.email,
            'enterprise_customer_uuid': str(self.enterprise_customer.uuid)
        }

        response = self.client.post(self.create_admin_by_email_url, data)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['uuid'], str(existing_admin.uuid))

        # Verify SystemWideEnterpriseUserRoleAssignment was created
        role_assignment = SystemWideEnterpriseUserRoleAssignment.objects.filter(
            user=self.target_user,
            role__name=ENTERPRISE_ADMIN_ROLE,
            enterprise_customer=self.enterprise_customer
        )
        self.assertTrue(role_assignment.exists())

    def test_create_admin_by_email_missing_email(self):
        """
        Test error response when email is missing.
        """
        self.set_jwt_cookie(SYSTEM_ENTERPRISE_PROVISIONING_ADMIN_ROLE, ALL_ACCESS_CONTEXT)

        data = {
            'enterprise_customer_uuid': str(self.enterprise_customer.uuid)
        }

        response = self.client.post(self.create_admin_by_email_url, data)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data['error'], 'email is required')

    def test_create_admin_by_email_missing_enterprise_customer_uuid(self):
        """
        Test error response when enterprise_customer_uuid is missing.
        """
        self.set_jwt_cookie(SYSTEM_ENTERPRISE_PROVISIONING_ADMIN_ROLE, ALL_ACCESS_CONTEXT)

        data = {
            'email': self.target_user.email
        }

        response = self.client.post(self.create_admin_by_email_url, data)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data['error'], 'enterprise_customer_uuid is required')

    def test_create_admin_by_email_user_not_found(self):
        """
        Test error response when user with email does not exist.
        """
        self.set_jwt_cookie(SYSTEM_ENTERPRISE_PROVISIONING_ADMIN_ROLE, ALL_ACCESS_CONTEXT)

        data = {
            'email': 'nonexistent@example.com',
            'enterprise_customer_uuid': str(self.enterprise_customer.uuid)
        }

        response = self.client.post(self.create_admin_by_email_url, data)

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(response.data['error'], 'User with email nonexistent@example.com does not exist')

    def test_create_admin_by_email_enterprise_customer_not_found(self):
        """
        Test error response when enterprise customer does not exist.
        """
        self.set_jwt_cookie(SYSTEM_ENTERPRISE_PROVISIONING_ADMIN_ROLE, ALL_ACCESS_CONTEXT)

        fake_uuid = '12345678-1234-5678-1234-567812345678'
        data = {
            'email': self.target_user.email,
            'enterprise_customer_uuid': fake_uuid
        }

        response = self.client.post(self.create_admin_by_email_url, data)

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(
            response.data['error'],
            f'EnterpriseCustomer with uuid {fake_uuid} does not exist'
        )

    def test_create_admin_by_email_permission_required(self):
        """
        Test that the endpoint requires proper permissions.
        """
        # Don't set JWT cookie - should fail due to lack of permissions
        data = {
            'email': self.target_user.email,
            'enterprise_customer_uuid': str(self.enterprise_customer.uuid)
        }

        response = self.client.post(self.create_admin_by_email_url, data)

        # This should fail due to lack of permissions
        self.assertIn(response.status_code, [status.HTTP_403_FORBIDDEN, status.HTTP_401_UNAUTHORIZED])

    @ddt.data(
        ENTERPRISE_ADMIN_ROLE,
        ENTERPRISE_LEARNER_ROLE,
        ENTERPRISE_OPERATOR_ROLE,
        SYSTEM_ENTERPRISE_CATALOG_ADMIN_ROLE,
    )
    def test_create_admin_by_email_forbidden_roles(self, role):
        """
        Test that system-wide roles other than provisioning role are not allowed.
        """
        self.set_jwt_cookie(role, ALL_ACCESS_CONTEXT)

        data = {
            'email': self.target_user.email,
            'enterprise_customer_uuid': str(self.enterprise_customer.uuid)
        }

        response = self.client.post(self.create_admin_by_email_url, data)

        # Should fail due to insufficient permissions
        self.assertIn(response.status_code, [status.HTTP_403_FORBIDDEN, status.HTTP_401_UNAUTHORIZED])
       
@ddt.ddt
class TestEnterpriseCustomerAdminListAPI(APITest):
    """
    Tests for GET /{enterprise-customer-uuid}/admins
    """
    def setUp(self):
        self.enterprise = EnterpriseCustomerFactory()
        self.user = UserFactory()
        self.ecu = EnterpriseCustomerUserFactory(
            enterprise_customer=self.enterprise,
            user_id=self.user.id,
            active=True
        )
        EnterpriseCustomerAdmin.objects.create(enterprise_customer_user=self.ecu)
        # Authenticate the user so they have access
        self.client.force_authenticate(user=self.user)

        self.list_url = reverse('enterprise-customer-admin-list') + f'?enterprise_customer_uuid={self.enterprise.uuid}'

    def test_list_admins_success(self):
        """
        Verify that the admin list endpoint returns a successful response
        with the expected admin data for a valid enterprise customer.
        """
        admin = EnterpriseCustomerAdmin.objects.first()
        response = self.client.get(self.list_url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data['results']), 1)

        admin_data = response.data['results'][0]
        self.assertEqual(admin_data['uuid'], str(admin.uuid))
        self.assertEqual(admin_data['email'], self.user.email)
        self.assertEqual(admin_data['status'], 'active')
        self.assertIsNotNone(admin_data['invited_date'])
        self.assertIsNotNone(admin_data['joined_date'])

    def test_soft_deleted_admins_excluded(self):
        """
        Ensure inactive admins are excluded from the admin list endpoint.
        """
        removed_user = UserFactory(email='removed@example.com')

        removed_ecu = EnterpriseCustomerUserFactory(
            enterprise_customer=self.enterprise,
            user_id=removed_user.id,
            active=False,
        )
        EnterpriseCustomerAdmin.objects.create(enterprise_customer_user=removed_ecu)
        active_admin = EnterpriseCustomerAdmin.objects.get(
            enterprise_customer_user__active=True
        )

        response = self.client.get(self.list_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data['results']), 1)
        self.assertEqual(response.data["results"][0]["uuid"], str(active_admin.uuid))

    def test_admin_list_permission_denied(self):
        """
        Verify that unauthorized users cannot access the admin list endpoint.
        """
        other_user = UserFactory()
        token = AccessToken.for_user(other_user)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")

        response = self.client.get(self.list_url)

        self.assertIn(
            response.status_code,
            [status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN]
        )
