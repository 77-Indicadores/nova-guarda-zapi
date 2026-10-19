"""Texto do termo de uso e consentimento enviado ao cooperado.

Extraído do PDF original (versão 1.0), com o nome da empresa corrigido para
Guarda Nova e sem o aviso de minuta. Qualquer mudança de conteúdo é decisão
da Guarda Nova (o documento recomenda revisão jurídica)."""

TITLE = 'TERMO DE USO E CONSENTIMENTO PARA COMUNICAÇÕES OPERACIONAIS'
SUBTITLE = 'Guarda Nova - Comunicações por WhatsApp, escala, check-in e registros operacionais'
SECTIONS: list[tuple[str, list[str]]] = [
    (
        '1. Identificação',
        [
            'Este termo regula o uso do canal de WhatsApp da Guarda Nova para comunicações operacionais com cooperados, parceiros ou profissionais vinculados às escalas e atendimentos gerenciados pela empresa.',
            'Ao selecionar a opção Li e aceito, o cooperado declara que leu este termo, compreendeu suas condições e autoriza o uso do WhatsApp informado para as finalidades descritas abaixo.',
        ],
    ),
    (
        '2. Finalidade do canal',
        [
            'O canal será utilizado exclusivamente para comunicações relacionadas à operação da Guarda Nova, incluindo: ativação do cadastro no WhatsApp, envio e confirmação de escalas, recusa de escala, lembretes operacionais, check-in, check-out, registro de atraso, registro de não comparecimento e orientações necessárias para execução do atendimento.',
            'Este canal não substitui contratos, políticas internas, normas de conduta, documentos trabalhistas, cooperativos ou comerciais eventualmente aplicáveis à relação entre as partes.',
        ],
    ),
    (
        '3. Dados tratados',
        [
            'Para funcionamento do fluxo, a Guarda Nova poderá tratar dados como nome, telefone, identificador do cooperado, dados de escala, data, horário, local de atendimento, resposta de confirmação ou recusa, status de check-in e check-out, previsão de atraso, motivo de não comparecimento, registros de mensagens e dados técnicos necessários para segurança, auditoria e rastreabilidade.',
            'Quando o cooperado confirmar chegada ao local, poderá ser solicitada ou registrada localização aproximada, endereço informado ou link de mapa, conforme recurso disponível no WhatsApp ou no sistema utilizado pela Guarda Nova.',
        ],
    ),
    (
        '4. Consulta e validação cadastral',
        [
            'Antes de ativar o fluxo, a Guarda Nova poderá consultar seus sistemas internos e/ou sistemas de gestão integrados para validar se o número de WhatsApp pertence a um cooperado autorizado e ativo. Caso o número não seja encontrado, esteja desatualizado ou pertença a cadastro inativo, o fluxo poderá ser bloqueado até regularização junto à equipe responsável.',
        ],
    ),
    (
        '5. Consentimento para mensagens',
        [
            'O cooperado autoriza a Guarda Nova a enviar mensagens pelo WhatsApp para as finalidades operacionais previstas neste termo. As mensagens poderão conter botões, listas, documentos, links, confirmações automáticas e solicitações de resposta relacionadas às escalas e atendimentos.',
            'O aceite não autoriza envio de propaganda não relacionada à operação, nem compartilhamento de dados para finalidades incompatíveis com este termo.',
        ],
    ),
    (
        '6. Check-in, check-out, atraso e não comparecimento',
        [
            'O check-in indica que o cooperado chegou ao local ou está apto a iniciar o atendimento, conforme orientação enviada pela Guarda Nova. O check-out indica a finalização do atendimento ou da atividade relacionada à escala.',
            'As opções de atraso e não comparecimento são registros operacionais internos da Guarda Nova. Essas informações poderão ser usadas para acompanhamento, suporte, auditoria e tomada de providências operacionais.',
        ],
    ),
    (
        '7. Responsabilidades do cooperado',
        [
            'O cooperado se compromete a manter seu número de WhatsApp atualizado, responder às solicitações de forma correta e tempestiva, não compartilhar mensagens sensíveis com terceiros não autorizados e comunicar a equipe da Guarda Nova em caso de erro, perda de acesso ao número, uso indevido do aparelho ou impossibilidade de comparecimento.',
        ],
    ),
    (
        '8. Segurança e retenção',
        [
            'A Guarda Nova adotará medidas razoáveis para proteger os dados tratados no fluxo, restringindo o acesso às pessoas e sistemas necessários para a operação. Os registros poderão ser mantidos pelo período necessário ao cumprimento das finalidades operacionais, obrigações legais, auditoria, prevenção de fraudes, defesa de direitos e controle interno.',
        ],
    ),
    (
        '9. Direitos do titular',
        [
            'Nos termos da legislação aplicável, especialmente a Lei Geral de Proteção de Dados Pessoais - LGPD, o cooperado poderá solicitar informações sobre o tratamento de seus dados, correção de dados incompletos ou desatualizados, revisão de permissões e demais direitos previstos em lei, observados os limites legais e operacionais aplicáveis.',
        ],
    ),
    (
        '10. Recusa ou revogação',
        [
            'Caso selecione Não aceito, o fluxo de comunicações operacionais pelo WhatsApp poderá permanecer bloqueado para este número. A recusa não impede que a Guarda Nova utilize outros canais necessários à operação, conforme cadastro e regras internas.',
            'A eventual revogação do consentimento poderá ser solicitada à equipe da Guarda Nova. A revogação poderá limitar ou impedir o uso de funcionalidades automatizadas de escala, check-in, check-out e registros operacionais pelo WhatsApp.',
        ],
    ),
    (
        '11. Atualizações deste termo',
        [
            'A Guarda Nova poderá atualizar este termo para refletir mudanças operacionais, técnicas, legais ou de integração com sistemas. Quando houver alteração relevante, poderá ser solicitado novo aceite pelo WhatsApp ou por outro canal oficial.',
        ],
    ),
    (
        '12. Declaração de aceite',
        [
            'Ao clicar em Li e aceito, o cooperado confirma que compreendeu este termo e autoriza a Guarda Nova a utilizar o WhatsApp informado para as comunicações e registros operacionais descritos.',
        ],
    ),
]
FOOTER = ['Empresa: Guarda Nova', 'Canal: WhatsApp operacional', 'Documento: Termo de uso e consentimento', 'Versão: 1.1 - 18/10/2026']
