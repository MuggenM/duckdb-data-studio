with source_data as (
    select 1 as id, 'Alice' as name, 'USA' as country
    union all
    select 2 as id, 'Bob' as name, 'UK' as country
    union all
    select 3 as id, 'Charlie' as name, 'Germany' as country
)
select * from source_data
