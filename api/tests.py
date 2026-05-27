from django.contrib.auth.models import User
from rest_framework import status
from rest_framework.test import APITestCase

from .models import Booking, Car, EmailLog, LogReport

class ApiEndpointTests(APITestCase):
	def test_register_user(self):
		payload = {
			'username': 'student1',
			'email': 'student1@example.com',
			'password': 'strongpass123',
		}
		response = self.client.post('/api/register/', payload, format='json')

		self.assertEqual(response.status_code, status.HTTP_201_CREATED)
		self.assertTrue(User.objects.filter(username='student1').exists())

	def test_me_accepts_email_query(self):
		User.objects.create_user(
			username='student4b',
			email='student4b@example.com',
			password='strongpass123',
		)

		response = self.client.get('/api/me/', {'email': 'student4b@example.com'})

		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(response.data['email'], 'student4b@example.com')

	def test_password_change_and_reset(self):
		user = User.objects.create_user(
			username='student5',
			email='student5@example.com',
			password='strongpass123',
		)

		change = self.client.post(
			'/api/password/change/',
			{
				'email': 'student5@example.com',
				'current_password': 'strongpass123',
				'new_password': 'newstrongpass123',
			},
			format='json',
		)
		self.assertEqual(change.status_code, status.HTTP_200_OK)

		reset = self.client.post('/api/password/reset/', {'email': 'student5@example.com'}, format='json')
		self.assertEqual(reset.status_code, status.HTTP_200_OK)
		self.assertTrue(EmailLog.objects.filter(recipient='student5@example.com').exists())

		user.refresh_from_db()
		self.assertTrue(user.check_password('newstrongpass123'))

	def test_booking_log_report_and_email_log_endpoints(self):
		user = User.objects.create_user(
			username='student6',
			email='student6@example.com',
			password='strongpass123',
		)

		owner = User.objects.create_user(
			username='owner_student6',
			email='owner_student6@example.com',
			password='strongpass123',
		)
		car = None
		# Ensure vehicleId=1 resolves by creating a real car with id=1.
		car = Car.objects.create(
			owner=owner,
			brand='Toyota',
			model='Vios',
			year=2022,
			daily_rate='1500.00',
			available=True,
		)

		booking_response = self.client.post(
			'/api/bookings/',
			{
				'vehicleId': car.id,
				'renterId': user.id,
				'ownerId': owner.id,
				'status': 'pending',
			},
			format='json',
		)
		self.assertEqual(booking_response.status_code, status.HTTP_201_CREATED)
		self.assertEqual(Booking.objects.count(), 1)

		booking_id = booking_response.data['id']
		booking_patch = self.client.patch(
			f'/api/bookings/{booking_id}/',
			{'status': 'approved'},
			format='json',
		)
		self.assertEqual(booking_patch.status_code, status.HTTP_200_OK)

		# When booking becomes approved, backend should auto-create a checkin log.
		self.assertEqual(LogReport.objects.count(), 1)
		self.assertTrue(LogReport.objects.filter(
			rental_id=Booking.objects.get(id=booking_id).rental_id,
			report_type='checkin',
			reporter_id=user.id,
			vehicle_id=car.id,
		).exists())

		report_response = self.client.post(
			'/api/log-reports/',
			{
				'type': 'checkin',
				'vehicleId': car.id,
				'reporterId': user.id,
				'issues': [],
				'notes': 'ok',
			},
			format='json',
		)
		self.assertEqual(report_response.status_code, status.HTTP_201_CREATED)

		email_response = self.client.post(
			'/api/email-logs/',
			{
				'to': 'student6@example.com',
				'subject': 'Hello',
				'body': 'Welcome',
				'type': 'notification',
			},
			format='json',
		)
		self.assertEqual(email_response.status_code, status.HTTP_201_CREATED)
		self.assertEqual(EmailLog.objects.count(), 1)

	def test_cannot_checkout_without_return_accepted(self):
		user = User.objects.create_user(
			username='student_check',
			email='student_check@example.com',
			password='strongpass123',
		)
		owner = User.objects.create_user(
			username='owner_check',
			email='owner_check@example.com',
			password='strongpass123',
		)
		car = Car.objects.create(
			owner=owner,
			brand='Toyota',
			model='Vios',
			year=2022,
			daily_rate='1500.00',
			available=True,
		)

		booking = Booking.objects.create(
			renter=user,
			owner=owner,
			vehicle=car,
			status='approved',
			rental_id='RNT-checkout-1',
		)

		# Create a checkin log (the checkout will be attempted via PATCH later)
		log = LogReport.objects.create(
			reporter=user,
			vehicle=car,
			rental_id=booking.rental_id,
			report_type='checkin',
			data={},
		)

		# Attempt to add checkout without having a return_accepted log => should fail.
		resp = self.client.patch(
			f'/api/log-reports/{log.id}/',
			{'checkout': {'odometer': 123}},
			format='json',
		)
		self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
		self.assertIn('detail', resp.data)
		self.assertIn('return vehicle is accepted', resp.data['detail'])

		# Now create the required return_accepted log.
		LogReport.objects.create(
			reporter=owner,
			vehicle=car,
			rental_id=booking.rental_id,
			report_type='return_accepted',
			data={},
		)

		# Should succeed now.
		resp2 = self.client.patch(
			f'/api/log-reports/{log.id}/',
			{'checkout': {'odometer': 124}},
			format='json',
		)
		self.assertEqual(resp2.status_code, status.HTTP_200_OK)


	def test_booking_invalid_renter_id_returns_400(self):
		response = self.client.post(
			'/api/bookings/',
			{
				'brand': 'Toyota',
				'model': 'Vios',
				'renterId': '123t1231',
				'year': 2022,
				'daily_rate': '1500.00',
				'available': True,
			},
			format='json',
		)

		self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
		self.assertIn('renterId', response.data)

	def test_filter_bookings_by_status(self):
		user = User.objects.create_user(
			username='student7',
			email='student7@example.com',
			password='strongpass123',
		)
		Booking.objects.create(renter=user, owner=None, vehicle=None, status='pending')
		Booking.objects.create(renter=user, owner=None, vehicle=None, status='approved')

		response = self.client.get('/api/bookings/?status=approved')
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(len(response.data), 1)
		self.assertEqual(response.data[0]['status'], 'approved')

	def test_filter_cars_by_rented_reserved_and_available(self):
		owner = User.objects.create_user(
			username='owner1',
			email='owner1@example.com',
			password='strongpass123',
		)
		Car.objects.create(owner=owner, brand='Toyota', model='Vios', year=2022, daily_rate='1200.00', available=False)
		Car.objects.create(owner=owner, brand='Honda', model='Civic', year=2023, daily_rate='1300.00', available=True)

		response_rented = self.client.get('/api/cars/?status=rented')
		self.assertEqual(response_rented.status_code, status.HTTP_200_OK)
		self.assertEqual(len(response_rented.data), 1)
		self.assertFalse(response_rented.data[0]['available'])

		response_reserved = self.client.get('/api/cars/?status=reserved')
		self.assertEqual(response_reserved.status_code, status.HTTP_200_OK)
		self.assertEqual(len(response_reserved.data), 1)
		self.assertFalse(response_reserved.data[0]['available'])

		response_available = self.client.get('/api/cars/?status=available')
		self.assertEqual(response_available.status_code, status.HTTP_200_OK)
		self.assertEqual(len(response_available.data), 1)
		self.assertTrue(response_available.data[0]['available'])

	def test_car_creation_is_fetchable(self):
		create_response = self.client.post(
			'/api/cars/',
			{
				'brand': 'Nissan',
				'model': 'Sentra',
				'year': 2024,
				'daily_rate': '1400.00',
				'available': True,
			},
			format='json',
		)
		self.assertEqual(create_response.status_code, status.HTTP_201_CREATED)
		car_id = create_response.data['id']

		list_response = self.client.get('/api/cars/')
		self.assertEqual(list_response.status_code, status.HTTP_200_OK)
		self.assertTrue(any(car['id'] == car_id for car in list_response.data))
