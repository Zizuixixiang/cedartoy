"""Call-time dependency and public-signature contracts for backend extraction."""

import inspect
import unittest
from unittest.mock import Mock, patch

import server
from cedar_backend import (
    account_email,
    account_lifecycle,
    account_recovery,
    accounts,
    admin_accounts,
    auth,
    duel_bridge,
    game_dispatch,
    mcp_dispatch,
    operit,
    player_identity,
    save_management,
)


MODULES = (
    auth, accounts, player_identity, account_email, account_recovery,
    admin_accounts, operit, save_management, account_lifecycle, game_dispatch,
    duel_bridge, mcp_dispatch,
)


class BackendExtractionTests(unittest.TestCase):
    def test_wrappers_forward_arguments_and_current_dependencies(self):
        """Replacing a server helper/constant after import must affect every call."""
        for module in MODULES:
            for name, implementation in inspect.getmembers(module, inspect.isfunction):
                if implementation.__module__ != module.__name__:
                    continue
                wrapper = inspect.unwrap(getattr(server, name))
                public = inspect.signature(wrapper)
                internal = inspect.signature(implementation)
                dependencies = set(internal.parameters) - set(public.parameters)
                with self.subTest(module=module.__name__, function=name):
                    positional = []
                    keywords = {}
                    for parameter in public.parameters.values():
                        value = object()
                        if parameter.kind in (parameter.POSITIONAL_ONLY, parameter.POSITIONAL_OR_KEYWORD):
                            positional.append(value)
                        elif parameter.kind == parameter.KEYWORD_ONLY:
                            keywords[parameter.name] = value
                        elif parameter.kind == parameter.VAR_POSITIONAL:
                            positional.extend((value, object()))
                        elif parameter.kind == parameter.VAR_KEYWORD:
                            keywords["fixture_payload"] = value
                            # Internal dependency names must remain legal payload keys.
                            keywords.update({dependency: object() for dependency in dependencies})
                    expected = dict(public.bind(*positional, **keywords).arguments)
                    for dependency in dependencies:
                        replacement = object()
                        with patch.object(server, dependency, replacement), patch.object(module, name) as moved:
                            wrapper(*positional, **keywords)
                            bound = internal.bind(*moved.call_args.args, **moved.call_args.kwargs)
                            self.assertIs(bound.arguments[dependency], replacement)
                            for argument, value in expected.items():
                                if isinstance(value, (tuple, dict)):
                                    self.assertEqual(bound.arguments[argument], value)
                                else:
                                    self.assertIs(bound.arguments[argument], value)
                    result = Mock()
                    with patch.object(module, name, return_value=result):
                        self.assertIs(wrapper(*positional, **keywords), result)

    def test_slot_and_guest_identity_edges(self):
        for value in (None, True, False, 0, -1, 6, [], {}, "no-slot"):
            self.assertEqual(server._normalize_save_slot(value), 1)
        for slot in range(1, 6):
            self.assertEqual(server._normalize_save_slot(str(slot)), slot)
            expected = "23" if slot == 1 else f"23:{slot}"
            self.assertEqual(server._account_slot_player_id(23, slot), expected)


if __name__ == "__main__":
    unittest.main()
