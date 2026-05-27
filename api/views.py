import logging
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.contrib.auth import authenticate, login
from django.contrib.auth.models import User
from django.shortcuts import get_object_or_404
from rest_framework import generics, permissions
from rest_framework import status
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Booking, Car, EmailLog, LogReport
from .serializers import (
	CarSerializer,
	BookingSerializer,
	EmailLogSerializer,
	LogReportSerializer,
	PasswordChangeSerializer,
	PasswordResetSerializer,
	UserRegisterSerializer,
	UserSerializer,
	UserUpdateSerializer,
)

def broadcast_sync_event(event_type, action, instance_id, payload):
    """
    Helper to send real-time updates to all connected clients via Django Channels.
    """
    channel_layer = get_channel_layer()
    if channel_layer:
        async_to_sync(channel_layer.group_send)(
            "sync_group",
            {
                "type": "sync_event",
                "data": {
                    "type": event_type,
                    "action": action,
                    "id": str(instance_id),
                    "payload": payload,
                },
            },
        )


class LoginView(APIView):
	permission_classes = [permissions.AllowAny]
	authentication_classes = []  # Bypass default auth to avoid CSRF check on login


	def post(self, request):
		# Support multiple payload shapes so mobile + frontend can both log in.
		# Do NOT log/expose the password.
		d = request.data or {}

		# email aliases
		email = (
			(d.get('email') or d.get('Email') or d.get('emailAddress') or d.get('loginEmail') or '')
			.strip()
			.lower()
		)
		# username aliases
		username = (
			(d.get('username') or d.get('userName') or d.get('login') or d.get('user') or '')
			.strip()
		)
		# password aliases
		password = d.get('password') or d.get('pass') or d.get('Password') or ''


		# Temporary debug info (helps identify why mobile fails)
		debug_input_keys = sorted(list(d.keys()))
		debug_has_email = bool(email)
		debug_has_username = bool(username)

		# (Optional) allow frontend/mobile to request debug details
		want_debug = str(d.get('debug', '')).lower() in {'1', 'true', 'yes', 'y'}



		if not password or (not email and not username):
			return Response(
				{'detail': 'Provide email or username along with password.'},
				status=status.HTTP_400_BAD_REQUEST,
			)

		user = None
		if email:
			user_obj = User.objects.filter(email__iexact=email).first()
			if user_obj:
				user = authenticate(request, username=user_obj.username, password=password)
		else:
			user = authenticate(request, username=username, password=password)

		if user is not None:
			login(request, user)
			return Response(UserSerializer(user).data)

		# Debug payload: do not include password.
		if want_debug:
			candidate_email_user = None
			candidate_username_user = None
			if email:
				candidate_email_user = User.objects.filter(email__iexact=email).first()
			if username:
				candidate_username_user = User.objects.filter(username__iexact=username).first()

			return Response(
				{
					'detail': 'Invalid credentials.',
					'inputKeys': debug_input_keys,
					'emailProvided': debug_has_email,
					'usernameProvided': debug_has_username,
					'emailNormalized': email or None,
					'usernameNormalized': username or None,
					'emailUserExists': bool(candidate_email_user),
					'usernameUserExists': bool(candidate_username_user),
				},
				status=status.HTTP_401_UNAUTHORIZED,
			)

		return Response({'detail': 'Invalid credentials.'}, status=status.HTTP_401_UNAUTHORIZED)



class RegisterView(generics.CreateAPIView):
	queryset = User.objects.all()
	serializer_class = UserRegisterSerializer
	permission_classes = [permissions.AllowAny]

	def perform_create(self, serializer):
		user = serializer.save()
		if getattr(user, 'profile', None) and user.profile.role == 'admin':
			user.is_staff = True
			user.save(update_fields=['is_staff'])
		EmailLog.objects.create(
			recipient=user.email,
			subject='Welcome to IDRMS',
			body='Your account was created successfully.',
			log_type='registration',
			data={'userId': user.id, 'username': user.username},
		)


class PasswordChangeView(APIView):
	permission_classes = [permissions.AllowAny]

	def post(self, request):
		serializer = PasswordChangeSerializer(data=request.data)
		serializer.is_valid(raise_exception=True)
		current_password = serializer.validated_data['current_password']
		new_password = serializer.validated_data['new_password']
		email = serializer.validated_data.get('email', '').strip().lower()
		username = serializer.validated_data.get('username', '').strip()

		user = None
		if email:
			user = User.objects.filter(email__iexact=email).first()
		elif username:
			user = User.objects.filter(username=username).first()
		else:
			return Response(
				{'detail': 'Provide email or username.'},
				status=status.HTTP_400_BAD_REQUEST,
			)

		if not user:
			return Response({'detail': 'User not found.'}, status=status.HTTP_404_NOT_FOUND)

		if not user.check_password(current_password):
			return Response({'detail': 'Current password is incorrect.'}, status=status.HTTP_400_BAD_REQUEST)

		user.set_password(new_password)
		user.save(update_fields=['password'])
		return Response({'detail': 'Password updated successfully.'})


class PasswordResetView(APIView):
	permission_classes = [permissions.AllowAny]

	def post(self, request):
		serializer = PasswordResetSerializer(data=request.data)
		serializer.is_valid(raise_exception=True)
		email = serializer.validated_data['email'].strip().lower()
		EmailLog.objects.create(
			recipient=email,
			subject='Password reset request',
			body='A password reset request was submitted.',
			log_type='password',
			data={'email': email},
		)
		return Response({'detail': 'If an account exists, reset instructions will be sent.'})


class MeView(APIView):
	permission_classes = [permissions.AllowAny]

	def _resolve_target_user(self, request):
		user_id = request.query_params.get('userId') or request.data.get('userId') or request.data.get('id')
		email = (request.query_params.get('email') or request.data.get('email') or '').strip().lower()
		username = (request.query_params.get('username') or request.data.get('username') or '').strip()

		if user_id:
			return User.objects.filter(pk=user_id).first()
		if email:
			return User.objects.filter(email__iexact=email).first()
		if username:
			return User.objects.filter(username=username).first()
		return None

	def get(self, request):
		user = self._resolve_target_user(request)
		if not user:
			return Response(
				{'detail': 'Provide userId, email, or username.'},
				status=status.HTTP_400_BAD_REQUEST,
			)
		return Response(UserSerializer(user).data)

	def patch(self, request):
		user = self._resolve_target_user(request)
		if not user:
			return Response(
				{'detail': 'Provide userId, email, or username.'},
				status=status.HTTP_400_BAD_REQUEST,
			)
		serializer = UserUpdateSerializer(instance=user, data=request.data, partial=True)
		serializer.is_valid(raise_exception=True)
		updated_user = serializer.save()
		return Response(UserSerializer(updated_user).data)


class BookingListCreateView(generics.ListCreateAPIView):
	queryset = Booking.objects.select_related('renter', 'owner', 'vehicle').all()
	serializer_class = BookingSerializer
	permission_classes = [permissions.AllowAny]

	def get_queryset(self):
		queryset = Booking.objects.select_related('renter', 'owner', 'vehicle').all()
		status_param = self.request.query_params.get('status')
		if status_param:
			return queryset.filter(status__iexact=status_param.strip())
		return queryset

	def perform_create(self, serializer):
		# If the request is authenticated, use the requesting user as the renter.
		# This avoids a 400 when the client omits renterId but the user is logged in.
		if getattr(self.request, 'user', None) and getattr(self.request.user, 'is_authenticated', False):
			serializer.save(renter=self.request.user)
		else:
			serializer.save()
		
		broadcast_sync_event(
			'booking_created', 
			'create', 
			serializer.instance.id, 
			BookingSerializer(serializer.instance).data
		)


class BookingDetailView(generics.RetrieveUpdateDestroyAPIView):
	queryset = Booking.objects.select_related('renter', 'owner', 'vehicle').all()
	serializer_class = BookingSerializer
	permission_classes = [permissions.AllowAny]

	def perform_update(self, serializer):
		# Capture previous status so we only react to transitions.
		instance = self.get_object()
		previous_status = instance.status

		instance = serializer.save()

		# If booking is accepted/approved -> create a log-book entry (checkin)
		# so “rental history” becomes available to the frontend.
		accepted_statuses = {'accepted', 'approved'}
		new_status = (instance.status or '').strip().lower()
		prev = (previous_status or '').strip().lower()

		if new_status in accepted_statuses and prev != new_status:
			# Ensure booking.rental_id exists for linkage with LogReport.
			if not instance.rental_id:
				instance.rental_id = f"RNT-{instance.id}"
				instance.save(update_fields=['rental_id'])

			# Create checkin log if it doesn’t already exist.
			if instance.vehicle_id and instance.renter_id:
				from .models import LogReport
				checkin_exists = LogReport.objects.filter(
					rental_id=instance.rental_id,
					report_type='checkin',
					reporter_id=instance.renter_id,
					vehicle_id=instance.vehicle_id,
				).exists()

				if not checkin_exists:
					LogReport.objects.create(
						reporter=instance.renter,
						vehicle=instance.vehicle,
						rental_id=instance.rental_id,
						report_type='checkin',
						data={
							'bookingId': instance.id,
							'bookingStatus': instance.status,
							'start_date': instance.start_date,
							'end_date': instance.end_date,
							'amount': str(instance.amount) if instance.amount is not None else None,
						},
						checkout=None,
						comments=[],
					)

				
			# When the owner accepts a return request, frontend needs to be able to
			# checkout the vehicle after trip.
			# This is blocked by LogReportDetailView unless a matching
			# `return_accepted` LogReport exists (same rental_id + vehicle_id).
			#
			# We create that log here when booking status transitions to completed-
			# style states. Adjust these statuses to match your UI.
			return_accepted_statuses = {'return_accepted', 'returnaccepted', 'returned', 'completed'}
			if new_status in return_accepted_statuses and prev != new_status:
				from .models import LogReport
				if instance.vehicle_id and instance.owner_id:
					# renter/owner choice: most flows use owner as reporter for return acceptance.
					return_accepted_exists = LogReport.objects.filter(
						rental_id=instance.rental_id,
						report_type='return_accepted',
						vehicle_id=instance.vehicle_id,
					).exists()

					if not return_accepted_exists:
						LogReport.objects.create(
							reporter=instance.owner,
							vehicle=instance.vehicle,
							rental_id=instance.rental_id or f"RNT-{instance.id}",
							report_type='return_accepted',
							data={
								'bookingId': instance.id,
								'bookingStatus': instance.status,
							},
							checkout=None,
							comments=[],
						)

		broadcast_sync_event(
			'booking_updated',
			'update',
			instance.id,
			BookingSerializer(instance).data
		)


	def perform_destroy(self, instance):
		instance_id = instance.id
		instance.delete()
		broadcast_sync_event(
			'booking_deleted', 
			'delete', 
			instance_id, 
			None
		)



class LogReportListCreateView(generics.ListCreateAPIView):
	# FIX: added 'reporter' and 'vehicle__owner' to select_related so that
	# LogReportSerializer.to_representation can read reporter.first_name /
	# reporter.last_name and vehicle.owner.* without extra DB queries.
	queryset = LogReport.objects.select_related('reporter', 'vehicle', 'vehicle__owner').all()
	serializer_class = LogReportSerializer
	permission_classes = [permissions.AllowAny]

	def get_queryset(self):
		queryset = LogReport.objects.select_related('reporter', 'vehicle', 'vehicle__owner').all()
		# Allow filtering by renterId / reporterId so the frontend can fetch
		# only the current user's reports without client-side filtering.
		renter_id = (
			self.request.query_params.get('renterId')
			or self.request.query_params.get('reporterId')
		)
		if renter_id:
			try:
				queryset = queryset.filter(reporter_id=int(renter_id))
			except (TypeError, ValueError):
				pass
		return queryset

	def perform_create(self, serializer):
		# If authenticated, use the requesting user as the reporter.
		if getattr(self.request, 'user', None) and getattr(self.request.user, 'is_authenticated', False):
			serializer.save(reporter=self.request.user)
		else:
			serializer.save()

		broadcast_sync_event(
			'logreport_created', 
			'create', 
			serializer.instance.id, 
			LogReportSerializer(serializer.instance).data
		)


class LogReportDetailView(generics.RetrieveUpdateDestroyAPIView):
	# FIX: same select_related as the list view
	queryset = LogReport.objects.select_related('reporter', 'vehicle', 'vehicle__owner').all()
	serializer_class = LogReportSerializer
	permission_classes = [permissions.AllowAny]

	def perform_update(self, serializer):
		# Auto-complete booking when checkout is created
		instance = self.get_object()
		validated_data = serializer.validated_data

		# BLOCK: checkout can only be added/changed after the owner accepted the return.
		if 'checkout' in validated_data and validated_data.get('checkout'):
			# If this update is trying to set checkout while no return_accepted exists, reject.
			if instance.rental_id and instance.vehicle_id:
				return_accepted_exists = LogReport.objects.filter(
					rental_id=instance.rental_id,
					report_type='return_accepted',
					vehicle_id=instance.vehicle_id,
				).exists()
				# If there is no return acceptance, forbid checkout.
				if not return_accepted_exists:
					from rest_framework.exceptions import ValidationError
					raise ValidationError({
						'detail': 'You cannot add check-out until the return vehicle is accepted.'
					})

		# If checkout is being set, mark booking as completed (best-effort)
		if 'checkout' in validated_data and validated_data.get('checkout') and instance.rental_id:
			from .models import Booking
			try:
				booking = Booking.objects.filter(rental_id=instance.rental_id).first()
				if booking and booking.status != 'completed':
					booking.status = 'completed'
					booking.save(update_fields=['status', 'updated_at'])
			except Exception as e:
				import logging
				logger = logging.getLogger(__name__)
				logger.warning(f'Failed to auto-complete booking for rental_id {instance.rental_id}: {e}')

		instance = serializer.save()
		broadcast_sync_event(
			'logreport_updated',
			'update',
			instance.id,
			LogReportSerializer(instance).data
		)


	def perform_destroy(self, instance):
		instance_id = instance.id
		instance.delete()
		broadcast_sync_event(
			'logreport_deleted', 
			'delete', 
			instance_id, 
			None
		)



class EmailLogListCreateView(generics.ListCreateAPIView):
	queryset = EmailLog.objects.all()
	serializer_class = EmailLogSerializer
	permission_classes = [permissions.AllowAny]


class EmailLogDetailView(generics.RetrieveUpdateDestroyAPIView):
	queryset = EmailLog.objects.all()
	serializer_class = EmailLogSerializer
	permission_classes = [permissions.AllowAny]


class UserListView(generics.ListAPIView):
	queryset = User.objects.all().select_related('profile').order_by('-date_joined')
	serializer_class = UserSerializer
	permission_classes = [permissions.AllowAny]


class UserDetailView(generics.RetrieveUpdateDestroyAPIView):
	queryset = User.objects.all().select_related('profile')
	serializer_class = UserUpdateSerializer
	permission_classes = [permissions.AllowAny]

	def update(self, request, *args, **kwargs):
		response = super().update(request, *args, **kwargs)
		return Response(UserSerializer(self.get_object()).data)


class CarListCreateView(generics.ListCreateAPIView):
	queryset = Car.objects.all()
	serializer_class = CarSerializer
	permission_classes = [permissions.AllowAny]
	parser_classes = [MultiPartParser, FormParser, JSONParser]

	def get_queryset(self):
		queryset = Car.objects.select_related('owner').all()
		status_param = self.request.query_params.get('status')
		owner_id = self.request.query_params.get('ownerId')
		if status_param:
			status_value = status_param.strip().lower()
			if status_value in ['rented', 'reserved']:
				queryset = queryset.filter(available=False)
			elif status_value == 'available':
				queryset = queryset.filter(available=True)
		if owner_id:
			try:
				queryset = queryset.filter(owner_id=int(owner_id))
			except (TypeError, ValueError):
				pass
		return queryset

	def perform_create(self, serializer):
		logger = logging.getLogger(__name__)
		user = getattr(self.request, 'user', None)
		auth = getattr(self.request, 'auth', None)
		try:
			data = dict(self.request.data)
			if 'image' in data and isinstance(data.get('image'), str) and len(data['image']) > 100:
				data['image'] = data['image'][:30] + '...[truncated]'
		except Exception:
			data = str(self.request.data)
		logger.warning('Car create attempt: user=%s authenticated=%s auth=%s data=%s',
					   user, bool(user and getattr(user, 'is_authenticated', False)), auth, data)

		instance = None
		if self.request.user and self.request.user.is_authenticated:
			instance = serializer.save(owner=self.request.user)
		else:
			owner_candidate = None
			try:
				owner_candidate = (
					self.request.data.get('owner')
					or self.request.data.get('ownerId')
					or self.request.data.get('owner_id')
				)
			except Exception:
				owner_candidate = None

			owner_obj = None
			if owner_candidate:
				try:
					cand = str(owner_candidate).strip()
					if cand.isdigit():
						owner_obj = User.objects.filter(pk=int(cand)).first()
					elif '@' in cand:
						owner_obj = User.objects.filter(email__iexact=cand).first()
					else:
						owner_obj = User.objects.filter(username__iexact=cand).first()
				except Exception:
					owner_obj = None

			if owner_obj:
				instance = serializer.save(owner=owner_obj)
			else:
				instance = serializer.save()

		broadcast_sync_event(
			'vehicle_created', 
			'create', 
			instance.id, 
			CarSerializer(instance).data
		)



class CarDetailView(generics.RetrieveUpdateDestroyAPIView):
	queryset = Car.objects.all()
	serializer_class = CarSerializer
	permission_classes = [permissions.AllowAny]
	parser_classes = [MultiPartParser, FormParser, JSONParser]

	def get_queryset(self):
		return Car.objects.select_related('owner').all()

	def perform_update(self, serializer):
		instance = serializer.save()
		broadcast_sync_event(
			'vehicle_updated', 
			'update', 
			instance.id, 
			CarSerializer(instance).data
		)

	def perform_destroy(self, instance):
		instance_id = instance.id
		instance.delete()
		broadcast_sync_event(
			'vehicle_deleted', 
			'delete', 
			instance_id, 
			None
		)


class HealthCheckView(APIView):
	permission_classes = [permissions.AllowAny]

	def get(self, request):
		return Response({'status': 'ok', 'message': 'IDRMS backend is running'})