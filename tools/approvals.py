# tools/approvals.py
"""Approval tools: get_pending_approvals, get_approval_details, make_approval_decision."""
import logging

import auth
from api.graphql import _execute_graphql_request, _execute_graphql_request_cached, _summarize_graphql_data
from helpers import build_error_response, build_success_response, validate_required_fields
from logging_config import with_function_logging
from mcp_instance import mcp
from rate_limiter import with_rate_limit

logger = logging.getLogger("server")


@with_rate_limit
@with_function_logging
@mcp.tool()
async def get_pending_approvals(
    workflow_step: str = None,
    summary_mode: bool = True,
) -> str:
    """
    Get pending approval survey questions from Omada GraphQL API.

    Optional parameters:
        workflow_step: Filter by workflow step (one of: "ManagerApproval", "ResourceOwnerApproval", "SystemOwnerApproval")
                      If not provided, returns all pending approvals
        summary_mode: If True (default), returns only key fields (workflowStep, workflowStepTitle, reason)
                     If False, returns all fields including surveyId and surveyObjectKey

    ⚠️ IMPORTANT FOR CLAUDE - DISPLAY TO USER:
    When presenting pending approvals to the user, you MUST ALWAYS include:
    - Resource Name (resourceAssignment.resource.name)
    - System Name (resourceAssignment.resource.system.name)
    - Workflow Step (workflowStep)
    - Reason/Justification (reason)

    These fields provide essential context for the user to understand what access
    is being requested and make informed approval decisions.

    Returns:
        JSON response with pending approval survey questions including resource and system details,
        or error message if the request fails
    """
    await auth.ensure_authenticated()
    impersonate_user = auth.get_user_identity()

    logger.debug(
        f"DEBUG: ENTRY - get_pending_approvals(workflow_step={workflow_step}, summary_mode={summary_mode})"
    )

    try:
        # Validate workflow_step if provided
        valid_workflow_steps = [
            "ManagerApproval",
            "ResourceOwnerApproval",
            "SystemOwnerApproval",
        ]
        if workflow_step and workflow_step not in valid_workflow_steps:
            return build_error_response(
                error_type="ValidationError",
                message=f"Invalid workflow_step '{workflow_step}'. Must be one of: {', '.join(valid_workflow_steps)}",
                impersonated_user=impersonate_user,
                workflow_step_filter=workflow_step,
            )

        # Build filter clause conditionally
        filter_clause = (
            f'(filters: {{workflowStep: {{filterValue: "{workflow_step}", operator: EQUALS}}}})'
            if workflow_step
            else ""
        )

        # Build GraphQL query with conditional filter
        query = f"""query myAccessRequestApprovalSurveyQuestions {{
  accessRequestApprovalSurveyQuestions{filter_clause} {{
    pages
    total
    data {{
      reason
      surveyId
      surveyObjectKey
      workflowStep
      history
      workflowStepTitle
      resourceAssignment {{
        resource {{
          id
          name
          system {{
            id
            name
          }}
          resourceType {{
            name
            id
          }}
        }}
      }}
    }}
  }}
}}"""

        logger.debug(f"GraphQL query: {query}")

        # Execute GraphQL request with version 3.0 and caching support
        result = await _execute_graphql_request_cached(
            query,
            graphql_version="3.0",
            use_cache=True,
        )

        if result["success"]:
            data = result["data"]
            # Extract approval questions from the GraphQL response
            if (
                "data" in data
                and "accessRequestApprovalSurveyQuestions" in data["data"]
            ):
                approval_questions = data["data"][
                    "accessRequestApprovalSurveyQuestions"
                ]
                questions_data = approval_questions.get("data", [])
                total = approval_questions.get("total", 0)
                pages = approval_questions.get("pages", 0)

                # Apply summarization if requested
                response_data = questions_data
                if summary_mode:
                    logger.debug(
                        f"Applying summarization to {len(questions_data)} pending approvals"
                    )
                    logger.debug(
                        f"Original data fields: {list(questions_data[0].keys()) if questions_data else []}"
                    )
                    response_data = _summarize_graphql_data(
                        questions_data, "PendingApproval"
                    )
                    logger.debug(
                        f"Summarized data fields: {list(response_data[0].keys()) if response_data else []}"
                    )

                return build_success_response(
                    data=response_data,
                    endpoint=result["endpoint"],
                    impersonated_user=impersonate_user,
                    workflow_step_filter=workflow_step if workflow_step else "none",
                    total_approvals=total,
                    pages=pages,
                    approvals_returned=len(response_data),
                    summary_mode=summary_mode,
                    approvals=response_data,
                )
            else:
                return build_error_response(
                    error_type="NoApprovalsFound",
                    message="No pending approvals found in response",
                    impersonated_user=impersonate_user,
                    workflow_step_filter=workflow_step if workflow_step else "none",
                    response=data,
                )
        else:
            # Handle GraphQL request failure using helper
            return build_error_response(
                error_type=result.get("error_type", "GraphQLError"),
                result=result,
                impersonated_user=impersonate_user,
                workflow_step_filter=workflow_step if workflow_step else "none",
            )

    except Exception as e:
        return build_error_response(
            error_type=type(e).__name__,
            message=str(e),
            impersonated_user=impersonate_user,
            workflow_step_filter=workflow_step if workflow_step else "none",
        )


@with_rate_limit
@with_function_logging
@mcp.tool()
async def get_approval_details(
    workflow_step: str = None,
) -> str:
    """
    Get FULL approval details including technical IDs (surveyId, surveyObjectKey) needed for making decisions.

    Use this function when you need to make an approval decision and need the technical IDs.
    This returns all fields including surveyId and surveyObjectKey which are required for make_approval_decision.

    Optional parameters:
        workflow_step: Filter by workflow step (one of: "ManagerApproval", "ResourceOwnerApproval", "SystemOwnerApproval")

    Returns:
        JSON response with FULL approval details including surveyId and surveyObjectKey
    """
    # ENTRY LOGGING
    logger.debug(
        f"DEBUG: ENTRY - get_approval_details(workflow_step={workflow_step})"
    )

    # Call get_pending_approvals with summary_mode=False to get all fields
    return await get_pending_approvals(
        workflow_step=workflow_step,
        summary_mode=False,  # Get full details including technical IDs
    )


@with_rate_limit
@with_function_logging
@mcp.tool()
async def make_approval_decision(
    survey_id: str,
    survey_object_key: str,
    decision: str,
) -> str:
    """
    Make an approval decision (APPROVE or REJECT) for an access request using Omada GraphQL API.

    ⚠️ CRITICAL SECURITY WARNING FOR CLAUDE ⚠️
    This function performs a PERMANENT approval/rejection that affects access control.
    You MUST get explicit human confirmation before calling this function.

    MANDATORY CONFIRMATION PROTOCOL:
    Before calling this function, you MUST:
    1. Display the complete request details to the user (requester, resource, reason, etc.)
    2. Ask explicitly: "Do you want to APPROVE, REJECT, or CANCEL this request?"
    3. Wait for the user's explicit response (APPROVE/REJECT/CANCEL)
    4. Only call this function after receiving APPROVE or REJECT from the user
    5. If user says CANCEL, do NOT call this function

    NEVER call this function without completing all steps above.
    The user must see the request details and explicitly type their decision.

    REQUIRED PARAMETERS (prompt user if missing):
        survey_id: The survey ID for the approval (e.g., "d67d8182-5d9e-466d-b9c2-d499a095e06a")
                  PROMPT: "Please provide the survey ID"
        survey_object_key: The survey object key for the approval (e.g., "40501018-04D0-4C67-A4BD-E698C109B60C")
                          PROMPT: "Please provide the survey object key"
        decision: The approval decision - must be either "APPROVE" or "REJECT"
                 PROMPT: "Please provide the decision (APPROVE or REJECT)"

    Returns:
        JSON response with approval submission result or error message
    """
    try:
        await auth.ensure_authenticated()
        impersonate_user = auth.get_user_identity()

        # Validate mandatory fields using helper
        error = validate_required_fields(
            survey_id=survey_id,
            survey_object_key=survey_object_key,
            decision=decision,
        )
        if error:
            return error

        # Validate decision value
        valid_decisions = ["APPROVE", "REJECT"]
        decision_upper = decision.strip().upper()
        if decision_upper not in valid_decisions:
            return build_error_response(
                error_type="ValidationError",
                message=f"Invalid decision '{decision}'. Must be one of: {', '.join(valid_decisions)}",
            )

        # Build GraphQL mutation
        mutation = f"""mutation makeApprovalDecision {{
  submitRequestQuestions(
    submitRequestQuestionsInput: {{
      accessApprovals: {{
        questions: {{
          decision: {decision_upper},
          surveyObjectKey: "{survey_object_key}"}},
          surveyId: "{survey_id}"}}
        }}
  ) {{
    questionsSuccessfullySubmitted
  }}
}}"""

        logger.debug(f"GraphQL mutation: {mutation}")

        # Execute GraphQL request with version 3.0
        result = await _execute_graphql_request(
            query=mutation,
            graphql_version="3.0",
        )

        if result["success"]:
            data = result["data"]
            # Extract submission result from the GraphQL response
            if "data" in data and "submitRequestQuestions" in data["data"]:
                submission_result = data["data"]["submitRequestQuestions"]
                questions_submitted = submission_result.get(
                    "questionsSuccessfullySubmitted", False
                )

                return build_success_response(
                    data={"questions_successfully_submitted": questions_submitted},
                    endpoint=result["endpoint"],
                    impersonated_user=impersonate_user,
                    survey_id=survey_id,
                    survey_object_key=survey_object_key,
                    decision=decision_upper,
                )
            elif "errors" in data:
                # Handle GraphQL errors
                return build_error_response(
                    error_type="GraphQLError",
                    message="GraphQL mutation failed",
                    impersonated_user=impersonate_user,
                    survey_id=survey_id,
                    survey_object_key=survey_object_key,
                    decision=decision_upper,
                    errors=data["errors"],
                    endpoint=result["endpoint"],
                )
            else:
                return build_error_response(
                    error_type="UnexpectedResponse",
                    message="Unexpected response format from submitRequestQuestions",
                    impersonated_user=impersonate_user,
                    survey_id=survey_id,
                    survey_object_key=survey_object_key,
                    decision=decision_upper,
                    response=data,
                )
        else:
            # Handle GraphQL request failure using helper
            return build_error_response(
                error_type=result.get("error_type", "GraphQLError"),
                result=result,
                impersonated_user=impersonate_user,
                survey_id=survey_id,
                survey_object_key=survey_object_key,
                decision=decision_upper,
            )

    except Exception as e:
        return build_error_response(
            error_type=type(e).__name__,
            message=str(e),
            impersonated_user=impersonate_user,
            survey_id=survey_id if "survey_id" in locals() else "N/A",
            survey_object_key=(
                survey_object_key if "survey_object_key" in locals() else "N/A"
            ),
            decision=decision if "decision" in locals() else "N/A",
        )
