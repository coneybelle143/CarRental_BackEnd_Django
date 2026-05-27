import base64
import binascii
import io
import uuid

from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from PIL import Image
from rest_framework import serializers
from .models import Booking, Car, EmailLog, LogReport, UserProfile


class Base64ImageField(serializers.ImageField):
    def to_internal_value(self, data):
        if isinstance(data, str):
            # Empty/whitespace string = no image provided
            if not data.strip():
                if self.required:
                    self.fail('required')
                return None

            # Already a URL (existing image echoed back from frontend) — keep existing, don't re-upload
            if data.startswith('http://') or data.startswith('https://') or data.startswith('/media/'):
                raise serializers.SkipField()

            # Strip base64 data URI prefix if present
            if data.startswith('data:') and ';base64,' in data:
                data = data.split(';base64,')[1]

            try:
                decoded_file = base64.b64decode(data)
            except (TypeError, binascii.Error):
                self.fail('invalid_image')

            file_name = str(uuid.uuid4())[:12]
            file_extension = self.get_file_extension(file_name, decoded_file)
            data = ContentFile(decoded_file, name=f"{file_name}.{file_extension}")

        return super().to_internal_value(data)

    def get_file_extension(self, file_name, decoded_file):
        try:
            image = Image.open(io.BytesIO(decoded_file))
            extension = image.format.lower()
        except Exception:
            extension = None
        if extension == 'jpeg':
            extension = 'jpg'
        return extension or 'png'


class UserSerializer(serializers.ModelSerializer):
    role = serializers.SerializerMethodField()
    middleName = serializers.SerializerMethodField()
    sex = serializers.SerializerMethodField()
    dateOfBirth = serializers.SerializerMethodField()
    firstName = serializers.CharField(source='first_name', allow_blank=True, required=False)
    lastName = serializers.CharField(source='last_name', allow_blank=True, required=False)
    fullName = serializers.SerializerMethodField()
    active = serializers.BooleanField(source='is_active', required=False)

    class Meta:
        model = User
        fields = (
            'id',
            'username',
            'email',
            'firstName',
            'lastName',
            'middleName',
            'fullName',
            'role',
            'sex',
            'dateOfBirth',
            'active',
        )

    def get_fullName(self, obj):
        profile = getattr(obj, 'profile', None)
        parts = [obj.first_name or '', getattr(profile, 'middle_name', '') or '', obj.last_name or '']
        full_name = ' '.join([part for part in parts if part]).strip()
        return full_name or obj.username

    def get_role(self, obj):
        profile = getattr(obj, 'profile', None)
        return getattr(profile, 'role', 'renter')

    def get_middleName(self, obj):
        profile = getattr(obj, 'profile', None)
        return getattr(profile, 'middle_name', '') or ''

    def get_sex(self, obj):
        profile = getattr(obj, 'profile', None)
        return getattr(profile, 'sex', '') or ''

    def get_dateOfBirth(self, obj):
        profile = getattr(obj, 'profile', None)
        return getattr(profile, 'date_of_birth', None)


class UserRegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, min_length=8)
    firstName = serializers.CharField(source='first_name', required=False, allow_blank=True)
    lastName = serializers.CharField(source='last_name', required=False, allow_blank=True)
    middleName = serializers.CharField(write_only=True, required=False, allow_blank=True)
    role = serializers.ChoiceField(choices=UserProfile.ROLE_CHOICES, required=False, default='renter')
    sex = serializers.ChoiceField(choices=UserProfile.SEX_CHOICES, required=False, allow_blank=True)
    dateOfBirth = serializers.DateField(write_only=True, required=False, allow_null=True)

    class Meta:
        model = User
        fields = (
            'id',
            'username',
            'email',
            'password',
            'firstName',
            'lastName',
            'middleName',
            'role',
            'sex',
            'dateOfBirth',
        )

    def validate(self, attrs):
        email = attrs.get('email', '').strip().lower()
        if not email:
            raise serializers.ValidationError({'email': 'Email is required.'})
        attrs['email'] = email

        if not attrs.get('username'):
            base_username = email.split('@')[0][:120] or 'user'
            candidate = base_username
            suffix = 1
            while User.objects.filter(username=candidate).exists():
                candidate = f"{base_username}{suffix}"
                suffix += 1
            attrs['username'] = candidate

        if User.objects.filter(email=email).exists():
            raise serializers.ValidationError({'email': 'Email is already registered.'})
        return attrs

    def create(self, validated_data):
        middle_name = validated_data.pop('middleName', '')
        role = validated_data.pop('role', 'renter')
        sex = validated_data.pop('sex', '')
        date_of_birth = validated_data.pop('dateOfBirth', None)

        user = User.objects.create_user(**validated_data)
        profile, _ = UserProfile.objects.get_or_create(user=user)
        profile.role = role
        profile.middle_name = middle_name
        profile.sex = sex
        profile.date_of_birth = date_of_birth
        profile.save()
        return user


class UserUpdateSerializer(serializers.ModelSerializer):
    firstName = serializers.CharField(source='first_name', required=False, allow_blank=True)
    lastName = serializers.CharField(source='last_name', required=False, allow_blank=True)
    middleName = serializers.CharField(required=False, allow_blank=True)
    sex = serializers.ChoiceField(choices=UserProfile.SEX_CHOICES, required=False, allow_blank=True)
    dateOfBirth = serializers.DateField(required=False, allow_null=True)
    active = serializers.BooleanField(source='is_active', required=False)

    class Meta:
        model = User
        fields = ('firstName', 'lastName', 'middleName', 'sex', 'dateOfBirth', 'active')

    def update(self, instance, validated_data):
        middle_name = validated_data.pop('middleName', None)
        sex = validated_data.pop('sex', None)
        date_of_birth = validated_data.pop('dateOfBirth', None)

        for field, value in validated_data.items():
            setattr(instance, field, value)
        instance.save()

        profile, _ = UserProfile.objects.get_or_create(user=instance)
        if middle_name is not None:
            profile.middle_name = middle_name
        if sex is not None:
            profile.sex = sex
        if date_of_birth is not None:
            profile.date_of_birth = date_of_birth
        profile.save()
        return instance


class CarSerializer(serializers.ModelSerializer):
    ownerId = serializers.IntegerField(source='owner.id', read_only=True)
    ownerEmail = serializers.EmailField(source='owner.email', read_only=True)
    owner = serializers.SerializerMethodField()
    brand = serializers.CharField(required=False)
    model = serializers.CharField(required=False)
    year = serializers.IntegerField(required=False)
    daily_rate = serializers.DecimalField(max_digits=8, decimal_places=2, required=False)
    name = serializers.CharField(source='model', required=False)
    pricePerDay = serializers.DecimalField(source='daily_rate', max_digits=8, decimal_places=2, required=False)
    # FIXED: Use Base64ImageField instead of plain ImageField
    # This handles the case where frontend sends back an existing image URL string during PATCH
    image = Base64ImageField(required=False, allow_null=True)
    imageUrl = serializers.SerializerMethodField(read_only=True)
    status = serializers.SerializerMethodField()
    updatedAt = serializers.DateTimeField(source='updated_at', read_only=True)
    type = serializers.CharField(source='vehicle_type', required=False, allow_blank=True)
    transmission = serializers.CharField(required=False, allow_blank=True)
    fuel = serializers.CharField(required=False, allow_blank=True)
    seats = serializers.IntegerField(required=False, allow_null=True)
    location = serializers.CharField(required=False, allow_blank=True)
    description = serializers.CharField(required=False, allow_blank=True)

    class Meta:
        model = Car
        fields = (
            'id',
            'owner',
            'ownerId',
            'ownerEmail',
            'brand',
            'model',
            'name',
            'year',
            'daily_rate',
            'pricePerDay',
            'image',
            'imageUrl',
            'available',
            'status',
            'type',
            'transmission',
            'fuel',
            'seats',
            'location',
            'description',
            'created_at',
            'updatedAt',
        )

    def get_owner(self, obj):
        if not obj.owner:
            return ''
        full_name = f"{obj.owner.first_name} {obj.owner.last_name}".strip()
        return full_name or obj.owner.username

    def get_imageUrl(self, obj):
        if not obj.image:
            return ''
        request = self.context.get('request')
        if request is not None:
            return request.build_absolute_uri(obj.image.url)
        return obj.image.url

    def to_representation(self, instance):
        data = super().to_representation(instance)
        image_url = self.get_imageUrl(instance)
        data['image'] = image_url
        data['imageUrl'] = image_url
        return data

    def get_status(self, obj):
        return 'available' if obj.available else 'rented'

    def create(self, validated_data):
        request = self.context.get('request')
        if request and request.user.is_authenticated and 'owner' not in validated_data:
            validated_data['owner'] = request.user
        return super().create(validated_data)


class PasswordChangeSerializer(serializers.Serializer):
    email = serializers.EmailField(required=False)
    username = serializers.CharField(required=False)
    current_password = serializers.CharField(write_only=True)
    new_password = serializers.CharField(write_only=True, min_length=8)


class PasswordResetSerializer(serializers.Serializer):
    email = serializers.EmailField()


class BookingSerializer(serializers.Serializer):
    id = serializers.IntegerField(read_only=True)
    status = serializers.CharField(required=False)
    createdAt = serializers.DateTimeField(read_only=True)
    updatedAt = serializers.DateTimeField(read_only=True)
    startDate = serializers.DateTimeField(required=False, allow_null=True)
    endDate = serializers.DateTimeField(required=False, allow_null=True)
    start_date = serializers.DateTimeField(required=False, allow_null=True)
    end_date = serializers.DateTimeField(required=False, allow_null=True)
    amount = serializers.DecimalField(max_digits=10, decimal_places=2, required=False, allow_null=True)

    def to_internal_value(self, data):
        return dict(data.items()) if hasattr(data, 'items') else dict(data)

    def _resolve_user(self, raw_value, field_name):
        if raw_value is None:
            return None
        if isinstance(raw_value, User):
            return raw_value

        if isinstance(raw_value, (list, tuple)):
            raw_value = raw_value[0] if raw_value else None
            if raw_value is None:
                return None

        text = str(raw_value).strip()
        if not text:
            return None

        if text.isdigit():
            return User.objects.filter(pk=int(text)).first()
        if '@' in text:
            return User.objects.filter(email__iexact=text).first()
        return User.objects.filter(username__iexact=text).first()

    def _resolve_car(self, raw_value, field_name):
        if raw_value is None:
            return None
        if isinstance(raw_value, Car):
            return raw_value

        if isinstance(raw_value, (list, tuple)):
            raw_value = raw_value[0] if raw_value else None
            if raw_value is None:
                return None

        try:
            return Car.objects.filter(pk=int(raw_value)).first()
        except (TypeError, ValueError):
            raise serializers.ValidationError({field_name: 'Must be a valid numeric vehicle id.'})

    def to_representation(self, instance):
        data = instance.data or {}

        # Hydrate names if missing in the JSON data field
        vehicle_name = data.get('vehicleName') or data.get('vehicle_name')
        if not vehicle_name and instance.vehicle:
            vehicle_name = f"{instance.vehicle.brand} {instance.vehicle.model}".strip()

        renter_name = data.get('renterName') or data.get('renter_name')
        if not renter_name and instance.renter:
            full = f"{instance.renter.first_name} {instance.renter.last_name}".strip()
            renter_name = full or instance.renter.username

        owner_name = data.get('ownerName') or data.get('owner_name')
        if not owner_name and instance.owner:
            full = f"{instance.owner.first_name} {instance.owner.last_name}".strip()
            owner_name = full or instance.owner.username

        return {
            **data,
            'id': instance.id,
            'renterId': instance.renter_id,
            'renterName': renter_name,
            'ownerId': instance.owner_id,
            'ownerName': owner_name,
            'vehicleId': instance.vehicle_id,
            'vehicleName': vehicle_name,
            'status': instance.status,
            'rentalId': instance.rental_id,
            'startDate': instance.start_date,
            'start_date': instance.start_date,
            'endDate': instance.end_date,
            'end_date': instance.end_date,
            'amount': instance.amount,
            'createdAt': instance.created_at,
            'updatedAt': instance.updated_at,
        }

    def create(self, validated_data):
        data = dict(validated_data)
        status_value = data.pop('status', 'pending')
        renter_id = data.pop('renterId', None) or data.pop('renter', None)
        owner_id = data.pop('ownerId', None) or data.pop('owner', None)
        vehicle_id = data.pop('vehicleId', None) or data.pop('vehicle', None)
        rental_id = data.pop('rental_id', '') or data.pop('rentalId', '')
        start_date = data.pop('startDate', None) or data.pop('start_date', None)
        end_date = data.pop('endDate', None) or data.pop('end_date', None)
        amount = data.pop('amount', None)

        renter = None
        request = getattr(self, 'context', {}).get('request')
        if renter_id is not None:
            renter = self._resolve_user(renter_id, 'renterId')
            if renter is None:
                raise serializers.ValidationError({'renterId': f'User not found for value {renter_id}.'})
        else:
            if request is not None and getattr(request, 'user', None) and getattr(request.user, 'is_authenticated', False):
                renter = request.user
            else:
                # No authenticated user and no renterId provided — attempt to auto-create or reuse a guest user
                guest_email = data.pop('renterEmail', None) or data.pop('email', None) or None
                if guest_email:
                    renter, _ = User.objects.get_or_create(email=guest_email, defaults={
                        'username': guest_email.split('@')[0][:150] or f'guest_{uuid.uuid4().hex[:8]}',
                    })
                else:
                    username = f'guest_{uuid.uuid4().hex[:8]}'
                    renter = User.objects.create_user(username=username, email='')

        owner = self._resolve_user(owner_id, 'ownerId')
        vehicle = self._resolve_car(vehicle_id, 'vehicleId')

        # Hydrate owner from vehicle if missing
        if not owner and vehicle:
            owner = vehicle.owner

        # Ensure JSONField `data` is JSON serializable.
        # Some clients may send model instances (e.g., User) inside `data`.
        # Strip them out to avoid: TypeError: Object of type User is not JSON serializable
        def _sanitize_for_json(value):
            if isinstance(value, User):
                return {'id': value.id, 'username': value.username, 'email': value.email}
            if isinstance(value, dict):
                return {k: _sanitize_for_json(v) for k, v in value.items()}
            if isinstance(value, (list, tuple)):
                return [_sanitize_for_json(v) for v in value]
            return value

        safe_data = _sanitize_for_json(data)

        return Booking.objects.create(
            renter=renter,
            owner=owner,
            vehicle=vehicle,
            status=status_value,
            rental_id=rental_id,
            start_date=start_date,
            end_date=end_date,
            amount=amount,
            data=safe_data,
        )

    def update(self, instance, validated_data):
        data = dict(instance.data or {})
        incoming = dict(validated_data)
        if 'status' in incoming:
            instance.status = incoming.pop('status')
        renter_id = incoming.pop('renterId', None) or incoming.pop('renter', None)
        owner_id = incoming.pop('ownerId', None) or incoming.pop('owner', None)
        vehicle_id = incoming.pop('vehicleId', None) or incoming.pop('vehicle', None)
        start_date = incoming.pop('startDate', None) or incoming.pop('start_date', None)
        end_date = incoming.pop('endDate', None) or incoming.pop('end_date', None)
        amount = incoming.pop('amount', None)
        
        if renter_id is not None:
            resolved_renter = self._resolve_user(renter_id, 'renterId')
            if resolved_renter is not None:
                instance.renter = resolved_renter
        if owner_id is not None:
            resolved_owner = self._resolve_user(owner_id, 'ownerId')
            if resolved_owner is not None:
                instance.owner = resolved_owner
        if vehicle_id is not None:
            resolved_vehicle = self._resolve_car(vehicle_id, 'vehicleId')
            if resolved_vehicle is not None:
                instance.vehicle = resolved_vehicle
        if 'rental_id' in incoming:
            instance.rental_id = incoming.pop('rental_id')
        if start_date is not None:
            instance.start_date = start_date
        if end_date is not None:
            instance.end_date = end_date
        if amount is not None:
            instance.amount = amount
        data.update(incoming)
        instance.data = data
        instance.save()
        return instance


class LogReportSerializer(serializers.Serializer):
    id = serializers.IntegerField(read_only=True)
    type = serializers.CharField(required=False)
    createdAt = serializers.DateTimeField(read_only=True)
    updatedAt = serializers.DateTimeField(read_only=True)
    checkout = serializers.JSONField(required=False, allow_null=True)
    comments = serializers.JSONField(required=False)

    def to_internal_value(self, data):
        return dict(data.items()) if hasattr(data, 'items') else dict(data)

    def _resolve_user(self, raw_value, field_name):
        if raw_value is None:
            return None
        if isinstance(raw_value, User):
            return raw_value

        if isinstance(raw_value, (list, tuple)):
            raw_value = raw_value[0] if raw_value else None
            if raw_value is None:
                return None

        text = str(raw_value).strip()
        if not text:
            return None

        if text.isdigit():
            return User.objects.filter(pk=int(text)).first()
        if '@' in text:
            return User.objects.filter(email__iexact=text).first()
        return User.objects.filter(username__iexact=text).first()

    def _resolve_car(self, raw_value, field_name):
        if raw_value is None:
            return None
        if isinstance(raw_value, Car):
            return raw_value

        if isinstance(raw_value, (list, tuple)):
            raw_value = raw_value[0] if raw_value else None
            if raw_value is None:
                return None

        try:
            return Car.objects.filter(pk=int(raw_value)).first()
        except (TypeError, ValueError):
            raise serializers.ValidationError({field_name: 'Must be a valid numeric vehicle id.'})

    def to_representation(self, instance):
        data = instance.data or {}

        # --- FIX: Build renterName directly from the reporter FK so the
        #     frontend filter can always match by name even if data{} is empty.
        renter_name = None
        if instance.reporter_id:
            reporter = instance.reporter  # already select_related in all views
            if reporter:
                full = f"{reporter.first_name} {reporter.last_name}".strip()
                renter_name = full or reporter.username

        # --- FIX: Build ownerName from the vehicle's owner if not already
        #     stored in data{} so the renter detail panel can display it.
        owner_name = data.get('ownerName')
        if not owner_name and instance.vehicle_id and instance.vehicle:
            owner = instance.vehicle.owner
            if owner:
                full = f"{owner.first_name} {owner.last_name}".strip()
                owner_name = full or owner.username

        # --- FIX: Normalise rentalId — model default is '' (blank string).
        #     Return None when blank so frontend String comparisons work cleanly.
        rental_id = instance.rental_id if instance.rental_id else None

        rep = {
            'id': instance.id,
            'type': instance.report_type,
            # Primary reporter FK (integer) — used as the most reliable filter key
            'renterId': instance.reporter_id,
            # Human-readable name derived from DB — fixes Condition B in the frontend filter
            'renterName': renter_name,
            'ownerName': owner_name,
            'vehicleId': instance.vehicle_id,
            # Normalised rentalId — fixes Condition A (was always '' before)
            'rentalId': rental_id,
            'checkout': instance.checkout,
            'comments': instance.comments or [],
            'createdAt': instance.created_at,
            'updatedAt': instance.updated_at,
        }

        # Merge the JSON data blob last so stored overrides (vehicleName, photos,
        # odometer, fuelLevel, etc.) win over the computed defaults above,
        # EXCEPT for the four fields we just fixed — protect those.
        protected = {'renterId', 'renterName', 'ownerName', 'rentalId'}
        for key, value in data.items():
            if key not in protected:
                rep[key] = value

        return rep

    def create(self, validated_data):
        data = dict(validated_data)
        report_type = data.pop('type', 'checkin')
        reporter_id = data.pop('reporterId', None) or data.pop('renterId', None) or data.pop('reporter', None)
        vehicle_id = data.pop('vehicleId', None) or data.pop('vehicle', None)
        rental_id = data.pop('rental_id', '') or data.pop('rentalId', '')
        checkout = data.pop('checkout', None)
        comments = data.pop('comments', [])
        reporter = None
        request = getattr(self, 'context', {}).get('request')
        if reporter_id is not None:
            reporter = self._resolve_user(reporter_id, 'reporterId')
            if reporter is None:
                raise serializers.ValidationError({'reporterId': f'User not found for value {reporter_id}.'})
        else:
            if request is not None and getattr(request, 'user', None) and getattr(request.user, 'is_authenticated', False):
                reporter = request.user
            else:
                guest_email = data.pop('reporterEmail', None) or data.pop('email', None) or None
                if guest_email:
                    reporter, _ = User.objects.get_or_create(email=guest_email, defaults={
                        'username': guest_email.split('@')[0][:150] or f'guest_{uuid.uuid4().hex[:8]}',
                    })
                else:
                    username = f'guest_{uuid.uuid4().hex[:8]}'
                    reporter = User.objects.create_user(username=username, email='')

        vehicle = self._resolve_car(vehicle_id, 'vehicleId')
        return LogReport.objects.create(
            reporter=reporter,
            vehicle=vehicle,
            rental_id=rental_id,
            report_type=report_type,
            data=data,
            checkout=checkout,
            comments=comments or [],
        )

    def update(self, instance, validated_data):
        data = dict(instance.data or {})
        incoming = dict(validated_data)
        if 'type' in incoming:
            instance.report_type = incoming.pop('type')
        reporter_id = incoming.pop('reporterId', None) or incoming.pop('renterId', None) or incoming.pop('reporter', None)
        vehicle_id = incoming.pop('vehicleId', None) or incoming.pop('vehicle', None)
        if reporter_id is not None:
            resolved_reporter = self._resolve_user(reporter_id, 'reporterId')
            if resolved_reporter is not None:
                instance.reporter = resolved_reporter
        if vehicle_id is not None:
            resolved_vehicle = self._resolve_car(vehicle_id, 'vehicleId')
            if resolved_vehicle is not None:
                instance.vehicle = resolved_vehicle
        if 'rental_id' in incoming:
            instance.rental_id = incoming.pop('rental_id')
        if 'rentalId' in incoming:
            instance.rental_id = incoming.pop('rentalId')
        if 'checkout' in incoming:
            instance.checkout = incoming.pop('checkout')
        if 'comments' in incoming:
            instance.comments = incoming.pop('comments')
        data.update(incoming)
        instance.data = data
        instance.save()
        return instance


class EmailLogSerializer(serializers.Serializer):
    id = serializers.IntegerField(read_only=True)
    type = serializers.CharField(required=False)
    sentAt = serializers.DateTimeField(read_only=True)

    def to_internal_value(self, data):
        return dict(data.items()) if hasattr(data, 'items') else dict(data)

    def to_representation(self, instance):
        data = instance.data or {}
        return {
            'id': instance.id,
            'to': instance.recipient,
            'subject': instance.subject,
            'body': instance.body,
            'type': instance.log_type,
            'status': instance.status,
            'sentAt': instance.sent_at,
            **data,
        }

    def create(self, validated_data):
        data = dict(validated_data)
        log_type = data.pop('type', 'notification')
        recipient = data.pop('to', data.pop('recipient', ''))
        subject = data.pop('subject', '')
        body = data.pop('body', '')
        status_value = data.pop('status', 'sent')
        return EmailLog.objects.create(
            recipient=recipient,
            subject=subject,
            body=body,
            log_type=log_type,
            status=status_value,
            data=data,
        )

    def update(self, instance, validated_data):
        data = dict(instance.data or {})
        incoming = dict(validated_data)
        if 'type' in incoming:
            instance.log_type = incoming.pop('type')
        if 'to' in incoming or 'recipient' in incoming:
            instance.recipient = incoming.pop('to', incoming.pop('recipient', instance.recipient))
        if 'subject' in incoming:
            instance.subject = incoming.pop('subject')
        if 'body' in incoming:
            instance.body = incoming.pop('body')
        if 'status' in incoming:
            instance.status = incoming.pop('status')
        data.update(incoming)
        instance.data = data
        instance.save()
        return instance