"""Restaurant-local cutoff for actual in-person occupancy versus projected use."""
def has_arrived(day,time,now):
    return (day,time)<=(now.date().isoformat(),now.strftime('%H:%M'))


def observed_sources(bookings,now):
    result=[]
    for booking in bookings:
        row=dict(booking)
        if row.get('source')=='walk_in' and not has_arrived(row['date'],row['time'],now):
            row.update(source='unknown',recorded_source='walk_in',source_note='Future in-person occupancy is unverified; capacity remains reserved.')
        result.append(row)
    return result
