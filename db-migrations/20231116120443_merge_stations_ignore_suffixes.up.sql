insert into visitors (station, visitor, tab, visited)
select split_part(station, '-', 1), visitor, tab, visited from visitors 
where station like '%-%'
on conflict do nothing;

delete from visitors where station like '%-%';
