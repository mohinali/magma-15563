"""
Copyright (c) 2016-present, Facebook, Inc.
All rights reserved.

This source code is licensed under the BSD-style license found in the
LICENSE file in the root directory of this source tree. An additional grant
of patent rights can be found in the PATENTS file in the same directory.
"""
import logging

from magma.pipelined.openflow import messages
from magma.pipelined.openflow.magma_match import MagmaMatch
from magma.pipelined.openflow.registers import SCRATCH_REGS, REG_ZERO_VAL

logger = logging.getLogger(__name__)

DEFAULT_PRIORITY = 10
MINIMUM_PRIORITY = 0
MAXIMUM_PRIORITY = 65535
OVS_COOKIE_MATCH_ALL = 0xffffffff


def add_flow(datapath, table, match, actions=None, instructions=None,
             priority=MINIMUM_PRIORITY, retries=3, cookie=0x0,
             idle_timeout=0, hard_timeout=0, resubmit_next_service=None,
             resubmit_current_service=None):
    """
    Add a flow to a table

    Args:
        datapath (ryu.controller.controller.Datapath):
            Datapath to push the flow to
        table (int): Table number to apply the flow to
        match (MagmaMatch): The match for the flow
        actions ([OFPAction]):
            List of actions for the flow.
        instructions ([OFPInstruction]):
            List of instructions for the flow. This will default to a
            single OFPInstructionsActions to apply `actions`.
            Ignored if `actions` is set.
        priority (int): Flow priority
        retries (int): Number of times to retry pushing the flow on failure
        cookie (hex): cookie value for the flow
        idle_timeout (int): idle timeout for the flow
        hard_timeout (int): hard timeout for the flow
        resubmit_next_service (int): Table number of the next service to
            forward traffic to. All scratch registers will be reset before
            resubmitting.
        resubmit_current_service (int): Table number of the table within the
            current service to forward traffic to. Scratch registers are not
            reset when resubmitting to the current service.

    Raises:
        MagmaOFError: if the flow can't be added
        Exception: If the actions contain NXActionResubmitTable.
            resubmit_next_service or resubmit_current_service should be used.
            Or if the flow is resubmitted to the next service and the actions
            contain an action that loads the scratch register. The scratch
            register is reset on table resubmit so any load has no effect.
    """
    mod = get_add_flow_msg(
        datapath, table, match, actions=actions,
        instructions=instructions, priority=priority,
        cookie=cookie, idle_timeout=idle_timeout, hard_timeout=hard_timeout,
        resubmit_next_service=resubmit_next_service,
        resubmit_current_service=resubmit_current_service)
    logger.debug('flowmod: %s (table %s)', mod, table)
    messages.send_msg(datapath, mod, retries)


def get_add_flow_msg(datapath, table, match, actions=None, instructions=None,
                     priority=MINIMUM_PRIORITY, cookie=0x0, idle_timeout=0,
                     hard_timeout=0, resubmit_next_service=None,
                     resubmit_current_service=None):
    """
    Get an add flow modification message

    Args:
        datapath (ryu.controller.controller.Datapath):
            Datapath to push the flow to
        table (int): Table number to apply the flow to
        match (MagmaMatch): The match for the flow
        actions ([OFPAction]):
            List of actions for the flow.
        instructions ([OFPInstruction]):
            List of instructions for the flow. This will default to a
            single OFPInstructionsActions to apply `actions`.
            Ignored if `actions` is set.
        priority (int): Flow priority
        cookie (hex): cookie value for the flow
        idle_timeout (int): idle timeout for the flow
        hard_timeout (int): hard timeout for the flow
        resubmit_next_service (int): Table number of the next service to
            forward traffic to. All scratch registers will be reset before
            resubmitting.
        resubmit_current_service (int): Table number of the table within the
            current service to forward traffic to. Scratch registers are not
            reset when resubmitting to the current service.

    Returns:
        OFPFlowMod

    Raises:
        Exception: If the actions contain NXActionResubmitTable.
            resubmit_next_service or resubmit_current_service should be used.
            Or if the flow is resubmitted to the next service and the actions
            contain an action that loads the scratch register. The scratch
            register is reset on table resubmit so any load has no effect.
    """
    ofproto, parser = datapath.ofproto, datapath.ofproto_parser

    if actions is None:
        actions = []
    _check_resubmit_action(actions, parser)
    if resubmit_next_service is not None:
        _check_scratch_reg_load(actions, parser)

        actions = actions + [
            parser.NXActionResubmitTable(table_id=resubmit_next_service),
        ]
        reset_scratch_reg_actions = [
            parser.NXActionRegLoad2(dst=reg, value=REG_ZERO_VAL)
            for reg in SCRATCH_REGS]
        actions = actions + reset_scratch_reg_actions
    elif resubmit_current_service is not None:
        actions = actions + [
            parser.NXActionResubmitTable(table_id=resubmit_current_service),
        ]

    inst = __get_instructions_for_actions(ofproto, parser,
                                          actions, instructions)
    ryu_match = parser.OFPMatch(**match.ryu_match)

    return parser.OFPFlowMod(datapath=datapath, priority=priority,
                             match=ryu_match, instructions=inst,
                             table_id=table, cookie=cookie,
                             idle_timeout=idle_timeout,
                             hard_timeout=hard_timeout)


def set_barrier(datapath):
    """
    Sends a barrier to the specified datapath to ensure all previous flows
    are pushed.

    Args:
        datapath (ryu.controller.controller.Datapath): Datapath to message.

    Raises:
        MagmaOFError: if barrier request fails
    """
    parser = datapath.ofproto_parser
    messages.send_msg(datapath, parser.OFPBarrierRequest(datapath))


def delete_flow(datapath, table, match, actions=None, instructions=None,
                retries=3, **kwargs):
    """
    Delete a flow from the given table

    Args:
        datapath (ryu.controller.controller.Datapath): Datapath to configure.
        table (int): table to delete the flow from
        match (MagmaMatch): match for the flow
        actions ([OFPAction]):
            Actions for the flow. Ignored if `instructions` is set.
        instructions ([OFPInstruction]):
            Instructions for the flow. This will default to a single
            OFPInstructionsActions for `actions`.
        retries (int): retry attempts on failure.

    Raises:
        MagmaOFError: if the flow can't be deleted
    """
    ofproto, parser = datapath.ofproto, datapath.ofproto_parser
    inst = __get_instructions_for_actions(ofproto, parser,
                                          actions, instructions)
    ryu_match = parser.OFPMatch(**match.ryu_match)

    mod = parser.OFPFlowMod(datapath=datapath, command=ofproto.OFPFC_DELETE,
                            match=ryu_match, instructions=inst,
                            table_id=table, out_group=ofproto.OFPG_ANY,
                            out_port=ofproto.OFPP_ANY,
                            **kwargs)
    logger.debug('flowmod: %s (table %s)', mod, table)
    messages.send_msg(datapath, mod, retries=retries)


def delete_all_flows_from_table(datapath, table, retries=3):
    """
    Delete all flows from a table.

    Args:
        datapath (ryu.controller.controller.Datapath): Datapath to configure
        table (int): Table to clear
        retries (int): retry attempts on failure

    Raises:
        MagmaOFError: if the flows can't be deleted
    """
    empty_match = MagmaMatch()
    delete_flow(datapath, table, empty_match, retries=retries)


def __get_instructions_for_actions(ofproto, ofproto_parser,
                                   actions, instructions):
    if actions and len(actions) > 0:
        return [
            ofproto_parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS,
                                                 actions),
        ]
    else:
        return instructions or []


def _check_scratch_reg_load(actions, parser):
    scratch_reg_load_action_exists = \
        actions is not None and \
        any(isinstance(action, parser.NXActionRegLoad2)
            and action.dst in SCRATCH_REGS for action in actions)
    if scratch_reg_load_action_exists:
        raise Exception(
            'Scratch register should not be loaded when '
            'resubmitting to another table owned by other apps',
        )


def _check_resubmit_action(actions, parser):
    resubmit_action_exists = \
        any(isinstance(action, parser.NXActionResubmitTable) for action in
            actions)
    if resubmit_action_exists:
        raise Exception(
            'Actions list should not contain NXActionResubmitTable',
        )
