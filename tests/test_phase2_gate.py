from __future__ import annotations

import unittest

from phase2_support import unhex, vectors
from the_grid.access import (
    AccessChallengeVerifier,
    AccessContext,
    AccessVerifierState,
    create_access_proof,
    derive_access_keys,
)
from the_grid.hub import BoardMessage, decrypt_board_record, encrypt_board_message
from the_grid.sessions import (
    HandshakeParticipant,
    LiveSessionChannel,
    SessionEventType,
    SessionRole,
    derive_session_phrase_material,
)


class PhaseTwoCompletionGateTests(unittest.TestCase):
    def test_access_board_and_live_session_cryptography_work_without_networking(self) -> None:
        data = vectors()
        access = data["access"]
        session = data["session"]
        context = AccessContext(
            unhex(access["server_id"]),
            unhex(access["access_generation"]),
        )
        keys = derive_access_keys(access["phrase"], context)

        state = AccessVerifierState(
            access_generation=context.access_generation,
            verifier_key=keys.authentication_key,
        )
        proof = create_access_proof(
            keys.authentication_key,
            context,
            challenge=unhex(access["challenge"]),
            client_nonce=unhex(access["client_nonce"]),
        )
        verifier = AccessChallengeVerifier(
            context,
            state,
            challenge=unhex(access["challenge"]),
        )
        self.assertTrue(
            verifier.verify(
                client_nonce=unhex(access["client_nonce"]),
                proof=proof,
            )
        )

        message = BoardMessage("ABC", "phase two gate")
        record = encrypt_board_message(
            message,
            context,
            keys,
            random_source=lambda length: bytes(range(length)),
        )
        self.assertEqual(decrypt_board_record(record, context, keys), message)

        phrase_material = derive_session_phrase_material(
            session["phrase"],
            server_id=context.server_id,
        )
        creator = HandshakeParticipant._from_test_values(
            SessionRole.CREATOR,
            private_key_bytes=unhex(session["creator_private_key"]),
            nonce=unhex(session["creator_nonce"]),
        )
        joiner = HandshakeParticipant._from_test_values(
            SessionRole.JOINER,
            private_key_bytes=unhex(session["joiner_private_key"]),
            nonce=unhex(session["joiner_nonce"]),
        )
        pair_id = unhex(session["pair_id"])
        creator_proof = creator.create_proof(
            phrase_material,
            server_id=context.server_id,
            pair_id=pair_id,
            peer_hello=joiner.hello,
        )
        joiner_proof = joiner.create_proof(
            phrase_material,
            server_id=context.server_id,
            pair_id=pair_id,
            peer_hello=creator.hello,
        )
        creator_material = creator.finalise(
            phrase_material,
            server_id=context.server_id,
            pair_id=pair_id,
            peer_hello=joiner.hello,
            peer_proof=joiner_proof,
        )
        joiner_material = joiner.finalise(
            phrase_material,
            server_id=context.server_id,
            pair_id=pair_id,
            peer_hello=creator.hello,
            peer_proof=creator_proof,
        )
        self.assertEqual(creator_material, joiner_material)

        creator_channel = LiveSessionChannel(creator_material, SessionRole.CREATOR)
        joiner_channel = LiveSessionChannel(joiner_material, SessionRole.JOINER)
        creator_id = creator_channel.encrypt_identity("ABC")
        joiner_id = joiner_channel.encrypt_identity("J7K")
        self.assertEqual(joiner_channel.decrypt_frame(creator_id).value, "ABC")
        self.assertEqual(creator_channel.decrypt_frame(joiner_id).value, "J7K")
        text = creator_channel.encrypt_text("encrypted through memory only")
        event = joiner_channel.decrypt_frame(text)
        self.assertEqual(event.event_type, SessionEventType.TEXT)
        self.assertEqual(event.value, "encrypted through memory only")


if __name__ == "__main__":
    unittest.main()
