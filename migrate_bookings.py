"""Import existing production bookings without modifying the legacy file."""
from receptionist import HERE, Tools, load_restaurant
import booking_db


def main():
    restaurant = load_restaurant(HERE / 'restaurants' / 'demo_restaurant.yaml')
    tools = Tools(restaurant)
    store = tools.booking_store()
    print(f'Database: {booking_db.DB_PATH}')
    print(f'Reservations: {len(store.list(tools.business_id))}')
    print('Legacy bookings file preserved. Re-running this command does not re-import it.')


if __name__ == '__main__':
    main()
