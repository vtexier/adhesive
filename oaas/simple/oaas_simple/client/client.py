import socket
from typing import Any, Optional

import oaas
from oaas import ClientDefinition
from oaas_grpc.client.client import OaasGrpcClient
from oaas_simple.client.service_client_proxy import ServiceClientProxy
from oaas_simple.rpc.call_pb2_grpc import ServiceInvokerStub


def is_someone_listening(host_address: str, port: int):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as a_socket:
        location = (host_address, port)
        try:
            result_of_check = a_socket.connect_ex(location)

            return result_of_check == 0
        except Exception:
            return False


class OaasSimpleClient(oaas.ClientMiddleware):
    def __init__(self) -> None:
        self._grpc_client: Optional[OaasGrpcClient] = None

        # FIXME: instantiating a client shouldn't magically add it
        oaas.register_client_provider(OaasGrpcClient())

    def create_client(self, client_definition: ClientDefinition) -> Any:
        client_proxy = self._oaas_grpc_client.create_grpc_client(
            namespace=client_definition.namespace,
            name=client_definition.name,
            version=client_definition.version,
            tags=client_definition.tags,
            code=ServiceInvokerStub,
        )

        return ServiceClientProxy(
            client_definition=client_definition, stub=client_proxy
        )

    def can_handle(self, client_definition: ClientDefinition) -> bool:
        """
        We only accept services that end with Stub, that are
        probably generated by gRPC.
        """
        name = client_definition.code.__name__

        return not name.endswith("Stub")

    @property
    def _oaas_grpc_client(self) -> OaasGrpcClient:
        if self._grpc_client:
            return self._grpc_client

        for client in oaas.registrations.clients_middleware:
            if isinstance(client, OaasGrpcClient):
                self._grpc_client = client
                return self._grpc_client

        self._grpc_client = OaasGrpcClient()
        return self._grpc_client
