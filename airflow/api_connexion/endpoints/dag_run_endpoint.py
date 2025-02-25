# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.
from typing import List, Optional, Tuple

import pendulum
from flask import current_app, g, request
from marshmallow import ValidationError
from sqlalchemy import or_
from sqlalchemy.orm import Query, Session

from airflow._vendor.connexion import NoContent
from airflow.api.common.mark_tasks import set_dag_run_state_to_failed, set_dag_run_state_to_success
from airflow.api_connexion import security
from airflow.api_connexion.exceptions import AlreadyExists, BadRequest, NotFound
from airflow.api_connexion.parameters import apply_sorting, check_limit, format_datetime, format_parameters
from airflow.api_connexion.schemas.dag_run_schema import (
    DAGRunCollection,
    dagrun_collection_schema,
    dagrun_schema,
    dagruns_batch_form_schema,
    set_dagrun_state_form_schema,
)
from airflow.api_connexion.types import APIResponse
from airflow.models import DagModel, DagRun
from airflow.security import permissions
from airflow.utils.session import NEW_SESSION, provide_session
from airflow.utils.state import DagRunState
from airflow.utils.types import DagRunType


@security.requires_access(
    [
        (permissions.ACTION_CAN_EDIT, permissions.RESOURCE_DAG),
        (permissions.ACTION_CAN_DELETE, permissions.RESOURCE_DAG_RUN),
    ],
)
@provide_session
def delete_dag_run(*, dag_id: str, dag_run_id: str, session: Session = NEW_SESSION) -> APIResponse:
    """Delete a DAG Run"""
    if session.query(DagRun).filter(DagRun.dag_id == dag_id, DagRun.run_id == dag_run_id).delete() == 0:
        raise NotFound(detail=f"DAGRun with DAG ID: '{dag_id}' and DagRun ID: '{dag_run_id}' not found")
    return NoContent, 204


@security.requires_access(
    [
        (permissions.ACTION_CAN_READ, permissions.RESOURCE_DAG),
        (permissions.ACTION_CAN_READ, permissions.RESOURCE_DAG_RUN),
    ],
)
@provide_session
def get_dag_run(*, dag_id: str, dag_run_id: str, session: Session = NEW_SESSION) -> APIResponse:
    """Get a DAG Run."""
    dag_run = session.query(DagRun).filter(DagRun.dag_id == dag_id, DagRun.run_id == dag_run_id).one_or_none()
    if dag_run is None:
        raise NotFound(
            "DAGRun not found",
            detail=f"DAGRun with DAG ID: '{dag_id}' and DagRun ID: '{dag_run_id}' not found",
        )
    return dagrun_schema.dump(dag_run)


def _fetch_dag_runs(
    query: Query,
    *,
    end_date_gte: Optional[str],
    end_date_lte: Optional[str],
    execution_date_gte: Optional[str],
    execution_date_lte: Optional[str],
    start_date_gte: Optional[str],
    start_date_lte: Optional[str],
    limit: Optional[int],
    offset: Optional[int],
    order_by: str,
) -> Tuple[List[DagRun], int]:
    if start_date_gte:
        query = query.filter(DagRun.start_date >= start_date_gte)
    if start_date_lte:
        query = query.filter(DagRun.start_date <= start_date_lte)
    # filter execution date
    if execution_date_gte:
        query = query.filter(DagRun.execution_date >= execution_date_gte)
    if execution_date_lte:
        query = query.filter(DagRun.execution_date <= execution_date_lte)
    # filter end date
    if end_date_gte:
        query = query.filter(DagRun.end_date >= end_date_gte)
    if end_date_lte:
        query = query.filter(DagRun.end_date <= end_date_lte)

    total_entries = query.count()
    to_replace = {"dag_run_id": "run_id"}
    allowed_filter_attrs = [
        "id",
        "state",
        "dag_id",
        "execution_date",
        "dag_run_id",
        "start_date",
        "end_date",
        "external_trigger",
        "conf",
    ]
    query = apply_sorting(query, order_by, to_replace, allowed_filter_attrs)
    return query.offset(offset).limit(limit).all(), total_entries


@security.requires_access(
    [
        (permissions.ACTION_CAN_READ, permissions.RESOURCE_DAG),
        (permissions.ACTION_CAN_READ, permissions.RESOURCE_DAG_RUN),
    ],
)
@format_parameters(
    {
        'start_date_gte': format_datetime,
        'start_date_lte': format_datetime,
        'execution_date_gte': format_datetime,
        'execution_date_lte': format_datetime,
        'end_date_gte': format_datetime,
        'end_date_lte': format_datetime,
        'limit': check_limit,
    }
)
@provide_session
def get_dag_runs(
    *,
    dag_id: str,
    start_date_gte: Optional[str] = None,
    start_date_lte: Optional[str] = None,
    execution_date_gte: Optional[str] = None,
    execution_date_lte: Optional[str] = None,
    end_date_gte: Optional[str] = None,
    end_date_lte: Optional[str] = None,
    state: Optional[List[str]] = None,
    offset: Optional[int] = None,
    limit: Optional[int] = None,
    order_by: str = "id",
    session: Session = NEW_SESSION,
):
    """Get all DAG Runs."""
    query = session.query(DagRun)

    #  This endpoint allows specifying ~ as the dag_id to retrieve DAG Runs for all DAGs.
    if dag_id == "~":
        appbuilder = current_app.appbuilder
        query = query.filter(DagRun.dag_id.in_(appbuilder.sm.get_readable_dag_ids(g.user)))
    else:
        query = query.filter(DagRun.dag_id == dag_id)

    if state:
        query = query.filter(DagRun.state.in_(state))

    dag_run, total_entries = _fetch_dag_runs(
        query,
        end_date_gte=end_date_gte,
        end_date_lte=end_date_lte,
        execution_date_gte=execution_date_gte,
        execution_date_lte=execution_date_lte,
        start_date_gte=start_date_gte,
        start_date_lte=start_date_lte,
        limit=limit,
        offset=offset,
        order_by=order_by,
    )
    return dagrun_collection_schema.dump(DAGRunCollection(dag_runs=dag_run, total_entries=total_entries))


@security.requires_access(
    [
        (permissions.ACTION_CAN_READ, permissions.RESOURCE_DAG),
        (permissions.ACTION_CAN_READ, permissions.RESOURCE_DAG_RUN),
    ],
)
@provide_session
def get_dag_runs_batch(*, session: Session = NEW_SESSION) -> APIResponse:
    """Get list of DAG Runs"""
    body = request.get_json()
    try:
        data = dagruns_batch_form_schema.load(body)
    except ValidationError as err:
        raise BadRequest(detail=str(err.messages))

    appbuilder = current_app.appbuilder
    readable_dag_ids = appbuilder.sm.get_readable_dag_ids(g.user)
    query = session.query(DagRun)
    if data.get("dag_ids"):
        dag_ids = set(data["dag_ids"]) & set(readable_dag_ids)
        query = query.filter(DagRun.dag_id.in_(dag_ids))
    else:
        query = query.filter(DagRun.dag_id.in_(readable_dag_ids))

    states = data.get("states")
    if states:
        query = query.filter(DagRun.state.in_(states))

    dag_runs, total_entries = _fetch_dag_runs(
        query,
        end_date_gte=data["end_date_gte"],
        end_date_lte=data["end_date_lte"],
        execution_date_gte=data["execution_date_gte"],
        execution_date_lte=data["execution_date_lte"],
        start_date_gte=data["start_date_gte"],
        start_date_lte=data["start_date_lte"],
        limit=data["page_limit"],
        offset=data["page_offset"],
        order_by=data.get("order_by", "id"),
    )

    return dagrun_collection_schema.dump(DAGRunCollection(dag_runs=dag_runs, total_entries=total_entries))


@security.requires_access(
    [
        (permissions.ACTION_CAN_EDIT, permissions.RESOURCE_DAG),
        (permissions.ACTION_CAN_CREATE, permissions.RESOURCE_DAG_RUN),
    ],
)
@provide_session
def post_dag_run(*, dag_id: str, session: Session = NEW_SESSION) -> APIResponse:
    """Trigger a DAG."""
    dm = session.query(DagModel).filter(DagModel.dag_id == dag_id).first()
    if not dm:
        raise NotFound(title="DAG not found", detail=f"DAG with dag_id: '{dag_id}' not found")
    if dm.has_import_errors:
        raise BadRequest(
            title="DAG cannot be triggered",
            detail=f"DAG with dag_id: '{dag_id}' has import errors",
        )
    try:
        post_body = dagrun_schema.load(request.json, session=session)
    except ValidationError as err:
        raise BadRequest(detail=str(err))

    logical_date = pendulum.instance(post_body["execution_date"])
    run_id = post_body["run_id"]
    dagrun_instance = (
        session.query(DagRun)
        .filter(
            DagRun.dag_id == dag_id,
            or_(DagRun.run_id == run_id, DagRun.execution_date == logical_date),
        )
        .first()
    )
    if not dagrun_instance:
        try:
            dag = current_app.dag_bag.get_dag(dag_id)
            dag_run = dag.create_dagrun(
                run_type=DagRunType.MANUAL,
                run_id=run_id,
                execution_date=logical_date,
                data_interval=dag.timetable.infer_manual_data_interval(run_after=logical_date),
                state=DagRunState.QUEUED,
                conf=post_body.get("conf"),
                external_trigger=True,
                dag_hash=current_app.dag_bag.dags_hash.get(dag_id),
            )
            return dagrun_schema.dump(dag_run)
        except ValueError as ve:
            raise BadRequest(detail=str(ve))

    if dagrun_instance.execution_date == logical_date:
        raise AlreadyExists(
            detail=(
                f"DAGRun with DAG ID: '{dag_id}' and "
                f"DAGRun logical date: '{logical_date.isoformat(sep=' ')}' already exists"
            ),
        )

    raise AlreadyExists(detail=f"DAGRun with DAG ID: '{dag_id}' and DAGRun ID: '{run_id}' already exists")


@security.requires_access(
    [
        (permissions.ACTION_CAN_READ, permissions.RESOURCE_DAG),
        (permissions.ACTION_CAN_EDIT, permissions.RESOURCE_DAG_RUN),
    ],
)
@provide_session
def update_dag_run_state(*, dag_id: str, dag_run_id: str, session: Session = NEW_SESSION) -> APIResponse:
    """Set a state of a dag run."""
    dag_run: Optional[DagRun] = (
        session.query(DagRun).filter(DagRun.dag_id == dag_id, DagRun.run_id == dag_run_id).one_or_none()
    )
    if dag_run is None:
        error_message = f'Dag Run id {dag_run_id} not found in dag {dag_id}'
        raise NotFound(error_message)
    try:
        post_body = set_dagrun_state_form_schema.load(request.json)
    except ValidationError as err:
        raise BadRequest(detail=str(err))

    state = post_body['state']
    dag = current_app.dag_bag.get_dag(dag_id)
    if state == DagRunState.SUCCESS:
        set_dag_run_state_to_success(dag=dag, run_id=dag_run.run_id, commit=True)
    else:
        set_dag_run_state_to_failed(dag=dag, run_id=dag_run.run_id, commit=True)
    dag_run = session.query(DagRun).get(dag_run.id)
    return dagrun_schema.dump(dag_run)
