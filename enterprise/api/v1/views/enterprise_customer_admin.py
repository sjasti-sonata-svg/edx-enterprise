"""
Views for `EnterpriseCustomerAdmin` model.
"""
from edx_rbac.decorators import permission_required
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from django.contrib.auth import get_user_model
from django.shortcuts import get_object_or_404

from enterprise import models, roles_api
from enterprise.api.v1.serializers import EnterpriseCustomerAdminListSerializer, EnterpriseCustomerAdminSerializer
from enterprise.constants import ENTERPRISE_CUSTOMER_PROVISIONING_ADMIN_ACCESS_PERMISSION
from enterprise.rules import has_implicit_access_to_provisioning_enterprise_customers

User = get_user_model()


class EnterpriseCustomerAdminPagination(PageNumberPagination):
    """
    Pagination class for EnterpriseCustomerAdmin viewset.
    """
    page_size = 10
    page_size_query_param = 'page_size'
    max_page_size = 100


class EnterpriseCustomerAdminViewSet(
    mixins.ListModelMixin,
    mixins.UpdateModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    """
    API views for the ``enterprise-customer-admin`` API endpoint.
    Only allows GET, and PATCH requests.
    """
    queryset = models.EnterpriseCustomerAdmin.objects.all()
    serializer_class = EnterpriseCustomerAdminSerializer
    permission_classes = (IsAuthenticated,)
    pagination_class = EnterpriseCustomerAdminPagination

    def get_queryset(self):
        """
        Filter queryset to only show records for the admin user.
        """
        return models.EnterpriseCustomerAdmin.objects.filter(
            enterprise_customer_user__user_fk=self.request.user,enterprise_customer_user__active=True,
        )

    @action(detail=True, methods=['post'])
    def complete_tour_flow(self, request, pk=None):  # pylint: disable=unused-argument
        """
        Add a completed tour flow to the admin's completed_tour_flows.
        POST /api/v1/enterprise-customer-admin/{pk}/complete_tour_flow/

        Request Arguments:
        - ``flow_uuid``: The request object containing the flow_uuid

        Returns: A response indicating success or failure
        """
        admin = self.get_object()
        flow_uuid = request.data.get('flow_uuid')

        if not flow_uuid:
            return Response(
                {'error': 'flow_uuid is required'},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            flow = get_object_or_404(models.OnboardingFlow, uuid=flow_uuid)
            admin.completed_tour_flows.add(flow)

            return Response({
                'status': 'success',
                'message': f'Successfully added tour flow {flow.title} to completed flows'
            })

        except models.OnboardingFlow.DoesNotExist:
            return Response(
                {'error': f'OnboardingFlow with uuid {flow_uuid} does not exist'},
                status=status.HTTP_404_NOT_FOUND
            )

    @permission_required(
        ENTERPRISE_CUSTOMER_PROVISIONING_ADMIN_ACCESS_PERMISSION,
        fn=lambda request, *args, **kwargs: request.data.get('enterprise_customer_uuid'),
    )
    @action(detail=False, methods=['post'])
    def create_admin_by_email(self, request):
        """
        Create a new EnterpriseCustomerAdmin record based on an email address.
        The email address must match an existing user.

        POST /api/v1/enterprise-customer-admin/create_admin_by_email/

        The requesting user must have the ``enterprise_provisioning_admin``
        role to access this endpoint.

        Request Arguments:
        - ``email``: Email address of the user to make an admin
        - ``enterprise_customer_uuid``: UUID of the enterprise customer

        Returns: A response with the created admin record.
        """
        email = request.data.get('email')
        enterprise_customer_uuid = request.data.get('enterprise_customer_uuid')

        if not email:
            return Response(
                {'error': 'email is required'}, status=status.HTTP_400_BAD_REQUEST,
            )

        if not enterprise_customer_uuid:
            return Response(
                {'error': 'enterprise_customer_uuid is required'}, status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            user = User.objects.get(email=email)
        except User.DoesNotExist:
            return Response(
                {'error': f'User with email {email} does not exist'}, status=status.HTTP_404_NOT_FOUND,
            )

        try:
            enterprise_customer = models.EnterpriseCustomer.objects.get(uuid=enterprise_customer_uuid)
        except models.EnterpriseCustomer.DoesNotExist:
            return Response(
                {'error': f'EnterpriseCustomer with uuid {enterprise_customer_uuid} does not exist'},
                status=status.HTTP_404_NOT_FOUND
            )

        # Get or create EnterpriseCustomerUser
        enterprise_customer_user, _ = models.EnterpriseCustomerUser.objects.get_or_create(
            enterprise_customer=enterprise_customer,
            user_fk=user,
            defaults={'user_id': user.id}
        )

        response_status_code = status.HTTP_200_OK
        admin, was_created = models.EnterpriseCustomerAdmin.objects.get_or_create(
            enterprise_customer_user=enterprise_customer_user
        )
        if was_created:
            response_status_code = status.HTTP_201_CREATED

        roles_api.assign_admin_role(
            enterprise_customer_user.user,
            enterprise_customer=enterprise_customer_user.enterprise_customer
        )

        serializer = self.get_serializer(admin)
        return Response(serializer.data, status=response_status_code)

    def list(self, request, *args, **kwargs):
        """
        List enterprise customer admins for a given enterprise.
        Requires provisioning admin access.
        """
        enterprise_customer_uuid = request.query_params.get(
            "enterprise_customer_uuid"
        )

        auth_header = request.META.get("HTTP_AUTHORIZATION", "")
        is_jwt_request = auth_header.startswith("Bearer ")

        #JWT requests MUST be enterprise-scoped
        if is_jwt_request:
            if not enterprise_customer_uuid:
                raise PermissionDenied()

            if not has_implicit_access_to_provisioning_enterprise_customers(
                request.user,
                enterprise_customer_uuid,
            ):
                raise PermissionDenied()
        #Legacy / ViewSet behavior (no enterprise filter)
        if not enterprise_customer_uuid:
            queryset = self.get_queryset()
            page = self.paginate_queryset(queryset)
            serializer = EnterpriseCustomerAdminListSerializer(page, many=True)
            return self.get_paginated_response(serializer.data)

        #Fetch enterprise
        enterprise = get_object_or_404(
            models.EnterpriseCustomer,
            uuid=enterprise_customer_uuid,
        )

        queryset = models.EnterpriseCustomerAdmin.objects.select_related(
            "enterprise_customer_user__user_fk"
        ).filter(
            enterprise_customer_user__enterprise_customer=enterprise,
            enterprise_customer_user__active=True,
        )

        page = self.paginate_queryset(queryset)
        serializer = EnterpriseCustomerAdminListSerializer(page, many=True)
        return self.get_paginated_response(serializer.data)

    @permission_required(
        ENTERPRISE_CUSTOMER_PROVISIONING_ADMIN_ACCESS_PERMISSION,
        fn=lambda request, *args, **kwargs: kwargs.get('enterprise_uuid'),
    )
    @action(detail=False, methods=['get'], url_path='enterprise/(?P<enterprise_uuid>[^/.]+)/admins')
    def list_enterprise_admins(self, request, enterprise_uuid=None):
        """
        GET /{enterprise-customer-uuid}/admins
        """
        admins_qs = models.EnterpriseCustomerAdmin.objects.select_related(
            'enterprise_customer_user__user_fk'
        ).filter(
            enterprise_customer_user__enterprise_customer__uuid=enterprise_uuid,
            enterprise_customer_user__active=True,
        )

        page = self.paginate_queryset(admins_qs)
        if page is not None:
            serializer = EnterpriseCustomerAdminListSerializer(page, many=True)
            return self.get_paginated_response(serializer.data)

        serializer = EnterpriseCustomerAdminListSerializer(admins_qs, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)
