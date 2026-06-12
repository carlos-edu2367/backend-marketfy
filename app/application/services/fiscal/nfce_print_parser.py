from __future__ import annotations

from xml.etree import ElementTree as ET

NFE_NS = {"nfe": "http://www.portalfiscal.inf.br/nfe"}


def _text(node: ET.Element | None, path: str) -> str | None:
    if node is None:
        return None
    found = node.find(path, NFE_NS)
    return found.text.strip() if found is not None and found.text else None


def parse_nfce_for_print(xml_bytes: bytes) -> dict:
    """Extract the fiscal print payload from an authorized NFC-e nfeProc XML."""
    root = ET.fromstring(xml_bytes)
    inf_nfe = root.find(".//nfe:NFe/nfe:infNFe", NFE_NS)
    if inf_nfe is None:
        raise ValueError("XML autorizado sem NFe/infNFe.")

    access_key = _text(root, ".//nfe:protNFe/nfe:infProt/nfe:chNFe")
    if not access_key:
        inf_id = inf_nfe.attrib.get("Id", "")
        access_key = inf_id[3:] if inf_id.startswith("NFe") else None

    issuer = root.find(".//nfe:NFe/nfe:infNFe/nfe:emit", NFE_NS)
    issuer_address = issuer.find("nfe:enderEmit", NFE_NS) if issuer is not None else None

    items = []
    for det in root.findall(".//nfe:NFe/nfe:infNFe/nfe:det", NFE_NS):
        prod = det.find("nfe:prod", NFE_NS)
        if prod is None:
            continue
        items.append(
            {
                "n_item": det.attrib.get("nItem"),
                "code": _text(prod, "nfe:cProd"),
                "description": _text(prod, "nfe:xProd"),
                "quantity": _text(prod, "nfe:qCom"),
                "unit": _text(prod, "nfe:uCom"),
                "unit_amount": _text(prod, "nfe:vUnCom"),
                "total_amount": _text(prod, "nfe:vProd"),
            }
        )

    payments = []
    for det_pag in root.findall(".//nfe:NFe/nfe:infNFe/nfe:pag/nfe:detPag", NFE_NS):
        payments.append(
            {
                "method": _text(det_pag, "nfe:tPag"),
                "amount": _text(det_pag, "nfe:vPag"),
            }
        )

    return {
        "access_key": access_key,
        "protocol": _text(root, ".//nfe:protNFe/nfe:infProt/nfe:nProt"),
        "authorized_at": _text(root, ".//nfe:protNFe/nfe:infProt/nfe:dhRecbto"),
        "sefaz_cstat": _text(root, ".//nfe:protNFe/nfe:infProt/nfe:cStat"),
        "sefaz_reason": _text(root, ".//nfe:protNFe/nfe:infProt/nfe:xMotivo"),
        "qr_code_url": _text(root, ".//nfe:NFe/nfe:infNFeSupl/nfe:qrCode"),
        "url_chave": _text(root, ".//nfe:NFe/nfe:infNFeSupl/nfe:urlChave"),
        "issuer": {
            "legal_name": _text(issuer, "nfe:xNome"),
            "trade_name": _text(issuer, "nfe:xFant"),
            "cnpj": _text(issuer, "nfe:CNPJ"),
            "state_registration": _text(issuer, "nfe:IE"),
            "address": {
                "street": _text(issuer_address, "nfe:xLgr"),
                "number": _text(issuer_address, "nfe:nro"),
                "district": _text(issuer_address, "nfe:xBairro"),
                "city": _text(issuer_address, "nfe:xMun"),
                "uf": _text(issuer_address, "nfe:UF"),
                "zip_code": _text(issuer_address, "nfe:CEP"),
            },
        },
        "series": _text(root, ".//nfe:NFe/nfe:infNFe/nfe:ide/nfe:serie"),
        "number": _text(root, ".//nfe:NFe/nfe:infNFe/nfe:ide/nfe:nNF"),
        "issued_at": _text(root, ".//nfe:NFe/nfe:infNFe/nfe:ide/nfe:dhEmi"),
        "total_amount": _text(root, ".//nfe:NFe/nfe:infNFe/nfe:total/nfe:ICMSTot/nfe:vNF"),
        "items": items,
        "payments": payments,
    }
