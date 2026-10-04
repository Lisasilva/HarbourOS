"""Throwaway: read-only stats to calibrate the unusual-behaviour checks."""

from HarbourOS.storage import connect

conn = connect()
Q = {
    "span": """select min(berth_start), max(berth_start), count(*) from fact_port_call""",
    "by_type": """select visit_type, completeness, count(*) n from fact_port_call group by all order by n desc""",
    "stay_by_cat": """
        select v.ship_category, count(*) n,
          round(quantile_cont(f.minutes_alongside, 0.5)) p50,
          round(quantile_cont(f.minutes_alongside, 0.9)) p90,
          round(quantile_cont(f.minutes_alongside, 0.99)) p99,
          max(f.minutes_alongside) mx
        from fact_port_call f join dim_vessel v using (mmsi)
        where f.visit_type='port_call' and f.completeness='complete'
        group by all order by n desc""",
    "port_cat_groups": """
        select count(*) groups, sum(n) calls, sum(case when n>=10 then 1 else 0 end) groups10,
          sum(case when n>=10 then n else 0 end) calls10
        from (select f.port_locode, v.ship_category, count(*) n
              from fact_port_call f join dim_vessel v using (mmsi)
              where f.visit_type='port_call' and f.completeness='complete' group by all)""",
    "atsea": """
        select v.ship_category, count(*) n, round(quantile_cont(nearest_port_km,0.5),1) p50km,
          round(quantile_cont(nearest_port_km,0.9),1) p90km, round(quantile_cont(f.minutes_alongside,0.5)) p50min
        from fact_port_call f join dim_vessel v using (mmsi)
        where f.visit_type='at_sea' group by all order by n desc""",
    "states": """select state, count(*), round(avg(epoch(end_time-start_time))/60) avg_min from ship_state_periods group by all""",
    "speed": """
        select v.ship_category, count(*) slots,
          round(quantile_cont(t.speed_over_ground,0.99),1) p99,
          round(quantile_cont(t.speed_over_ground,0.999),1) p999, max(t.speed_over_ground) mx,
          sum(case when t.speed_over_ground>40 then 1 else 0 end) over40
        from fct_vessel_track t join dim_vessel v using (mmsi) group by all order by slots desc""",
    "jumps": """
        with t as (select mmsi, message_time, latitude, longitude,
            lag(message_time) over w pt, lag(latitude) over w pla, lag(longitude) over w plo
            from fct_vessel_track window w as (partition by mmsi order by message_time))
        select count(*) pairs,
          sum(case when kn>40 then 1 else 0 end) over40kn,
          sum(case when kn>100 then 1 else 0 end) over100kn,
          count(distinct case when kn>40 then mmsi end) ships40
        from (select mmsi, 6371*2*asin(sqrt(pow(sin(radians(latitude-pla)/2),2)+cos(radians(pla))*cos(radians(latitude))*pow(sin(radians(longitude-plo)/2),2)))/1.852
               / nullif(epoch(message_time-pt)/3600,0) kn
              from t where pt is not null and epoch(message_time-pt) between 300 and 1800)""",
    "gaps": """
        with t as (select mmsi, epoch(message_time - lag(message_time) over (partition by mmsi order by message_time))/60 g from fct_vessel_track)
        select round(quantile_cont(g,0.5)) p50, round(quantile_cont(g,0.99)) p99, sum(case when g>180 then 1 else 0 end) over3h from t where g is not null""",
    "ships_week": """select count(distinct mmsi) from fct_vessel_track""",
    "silver_cols": """select column_name from duckdb_columns() where table_name='ais_messages_silver'""",
    "daily_ships": """
        select v.ship_category, count(*) ship_days from (
          select mmsi, date_trunc('day', message_time) d from fct_vessel_track group by all) x
        join dim_vessel v using (mmsi) group by all order by 2 desc""",
}
for name, sql in Q.items():
    print(f"== {name}")
    try:
        for row in conn.execute(sql).fetchall():
            print("  ", row)
    except Exception as e:
        print("   ERROR", e)
