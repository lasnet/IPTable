from ipaddress import IPv4Address, IPv4Network, ip_address, ip_network
from typing import Iterator


class NetworkValidationError(ValueError):
    pass


def normalize_cidr(cidr: str, max_addresses: int) -> str:
    try:
        network = ip_network(cidr.strip(), strict=False)
    except ValueError as exc:
        raise NetworkValidationError("CIDR указан в неверном формате") from exc

    if network.version != 4:
        raise NetworkValidationError("Сейчас поддерживаются только IPv4-подсети")

    if network.num_addresses > max_addresses:
        raise NetworkValidationError(
            f"Подсеть содержит {network.num_addresses} адресов, лимит: {max_addresses}"
        )

    return str(network)


def iter_project_addresses(cidr: str) -> Iterator[tuple[int, str]]:
    network = ip_network(cidr, strict=False)
    if not isinstance(network, IPv4Network):
        raise NetworkValidationError("Сейчас поддерживаются только IPv4-подсети")

    for ordinal, address in enumerate(network.hosts(), start=1):
        yield ordinal, str(address)


def reserved_project_addresses(cidr: str) -> set[str]:
    network = ip_network(cidr, strict=False)
    if not isinstance(network, IPv4Network) or network.prefixlen >= 31:
        return set()

    return {str(network.network_address), str(network.broadcast_address)}


def address_sort_key(address: str) -> IPv4Address:
    parsed = ip_address(address)
    if not isinstance(parsed, IPv4Address):
        raise NetworkValidationError("Сейчас поддерживаются только IPv4-адреса")
    return parsed
