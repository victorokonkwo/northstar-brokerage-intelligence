{#-
  Incremental builds that stay exact under late data (roadmap 4F-b, owner
  decision 2026-11-20). docs/architecture/incremental-models.md has the design.

  - Every dbt run or build is numbered in dbt_build_log (on-run-start).
  - int_arrivals records each raw record once, with the number of the build
    that first saw it, the first business date it can affect, and its keys.
  - Every incremental model records its successful builds in dbt_model_builds,
    in a post-hook inside the model's own transaction.
  - A model then rebuilds only what the records that arrived after its last
    successful build affect. Under the `rebuild` strategy below, that is either
    every row from the earliest affected date onward (meta.rebuild.date), or
    every row of the affected keys (meta.rebuild.key). It rebuilds everything
    when it has no recorded build, or when its SQL file changed since the last
    one.

  The build numbers order builds without a clock, so a failed build is simply
  picked up by the next one. Builds of one schema must not overlap: the Airflow
  warehouse pool and the single cloud execution already ensure that.
-#}

{% macro build_log_relation() -%}
    {{ target.schema }}.dbt_build_log
{%- endmacro %}

{% macro model_builds_relation() -%}
    {{ target.schema }}.dbt_model_builds
{%- endmacro %}

{% macro registers_builds() -%}
    {{ return(execute and flags.WHICH in ['run', 'build', 'retry']) }}
{%- endmacro %}

{#- on-run-start hooks, one statement each (empty for commands that build nothing). -#}

{% macro create_build_schema() -%}
    {%- if registers_builds() -%}
        create schema if not exists {{ target.schema }}
    {%- endif -%}
{%- endmacro %}

{% macro create_build_log() -%}
    {%- if registers_builds() -%}
        create table if not exists {{ build_log_relation() }} (
            build_seq integer not null primary key,
            invocation_id varchar(36) not null unique,
            dbt_started_at timestamp with time zone not null
        )
    {%- endif -%}
{%- endmacro %}

{% macro create_model_builds() -%}
    {%- if registers_builds() -%}
        create table if not exists {{ model_builds_relation() }} (
            model_name varchar(256) not null,
            build_seq integer not null,
            code_checksum varchar(64) not null,
            primary key (model_name, build_seq)
        )
    {%- endif -%}
{%- endmacro %}

{% macro register_build() -%}
    {%- if registers_builds() -%}
        insert into {{ build_log_relation() }} (build_seq, invocation_id, dbt_started_at)
        select
            coalesce(max(build_seq), 0) + 1,
            '{{ invocation_id }}',
            cast('{{ run_started_at.isoformat() }}' as timestamp with time zone)
        from {{ build_log_relation() }}
    {%- endif -%}
{%- endmacro %}

{#- The number of the current build: null when it was not registered. -#}
{% macro current_build_seq() -%}
    (select build_log.build_seq from {{ build_log_relation() }} as build_log where build_log.invocation_id = '{{ invocation_id }}')
{%- endmacro %}

{#- Post-hook of every incremental model, inside its transaction. -#}
{% macro record_build() -%}
    insert into {{ model_builds_relation() }} (model_name, build_seq, code_checksum)
    select '{{ this.identifier }}', build_log.build_seq, '{{ model.checksum.checksum }}'
    from {{ build_log_relation() }} as build_log
    where build_log.invocation_id = '{{ invocation_id }}'
{%- endmacro %}

{% macro last_build_seq() -%}
    (select coalesce(max(last_build.build_seq), 0) from {{ model_builds_relation() }} as last_build where last_build.model_name = '{{ this.identifier }}')
{%- endmacro %}

{#- True when the model has no recorded build, or its SQL changed since the last one. -#}
{% macro rebuilds_everything() -%}
    not exists (
        select 1 from {{ model_builds_relation() }} as model_build
        where
            model_build.model_name = '{{ this.identifier }}'
            and model_build.build_seq = {{ last_build_seq() }}
            and model_build.code_checksum = '{{ model.checksum.checksum }}'
    )
{%- endmacro %}

{#-
  The rows to rebuild, as a boolean SQL expression. In the model, pass the
  column as the final select names it (for example 'o.settlement_id'); the
  strategy uses the model's own column, from meta.rebuild.

  meta.rebuild is one of:
    {date: <column>, inputs: [<staging models>]}  every row on or after the earliest
        date any new record of those models can affect (a null effective date
        means from the beginning);
    {key: <column>, arrival: <int_arrivals column>, inputs: [...]}  every row whose
        key a new record of those models carries;
    {new: true}  int_arrivals itself, which finds new records by key.
-#}
{% macro rebuild_filter(column=none) -%}
    {%- set rebuild = config.get('meta', {}).get('rebuild') -%}
    {%- if rebuild is none -%}
        {{ exceptions.raise_compiler_error(this.identifier ~ ': meta.rebuild is not configured') }}
    {%- endif -%}
    {%- if rebuild.get('new') -%}
        ({{ rebuilds_everything() }})
    {%- else -%}
        {%- set new_arrivals -%}
            from {{ ref('int_arrivals') }} as new_arrival
            where
                new_arrival.source_model in ('{{ rebuild['inputs'] | join("', '") }}')
                and new_arrival._build_seq > {{ last_build_seq() }}
        {%- endset -%}
        {%- if 'date' in rebuild -%}
            ({{ rebuilds_everything() }}
            or {{ column or rebuild['date'] }} >= (
                select min(coalesce(new_arrival.effective_date, cast('0001-01-01' as date))) {{ new_arrivals }}
            ))
        {%- else -%}
            ({{ rebuilds_everything() }}
            or cast({{ column or rebuild['key'] }} as {{ string_type() }}) in (
                select new_arrival.{{ rebuild['arrival'] }} {{ new_arrivals }}
            ))
        {%- endif -%}
    {%- endif -%}
{%- endmacro %}

{#-
  Custom incremental strategy (incremental_strategy='rebuild'). Built-in
  delete+insert deletes only the rows whose key comes back, so a row that late
  data removes (a position closed by a late sell) would survive. This deletes
  every row in the rebuilt range and inserts the recomputed rows, in the
  model's transaction.
-#}
{% macro get_incremental_rebuild_sql(arg_dict) -%}
    {%- set columns = get_quoted_csv(arg_dict['dest_columns'] | map(attribute='name')) -%}
    delete from {{ arg_dict['target_relation'] }}
    where {{ rebuild_filter() }};

    insert into {{ arg_dict['target_relation'] }} ({{ columns }})
    select {{ columns }} from {{ arg_dict['temp_relation'] }}
{%- endmacro %}
