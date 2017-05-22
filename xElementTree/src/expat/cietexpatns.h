/* Copyright (c) 2005-2006 ActiveState Software Inc.
 *
 * Namespace all expat exported symbols to avoid dynamic loading symbol
 * collisions when embedding Python.
 *
 * The Problem:
 * - you embed Python in some app
 * - the app dynamically loads libexpat of version X
 * - the embedded Python imports pyexpat (which was built against
 *   libexpat version X+n)
 * --> pyexpat gets the expat symbols from the already loaded and *older*
 *     libexpat: crash (Specifically the crash we observed was in
 *     getting an old XML_ErrorString (from xmlparse.c) and then calling
 *     it with newer values in the XML_Error enum:
 *
 *       // pyexpat.c, line 1970
 *       ...
 *       // Added in Expat 1.95.7.
 *       MYCONST(XML_ERROR_UNBOUND_PREFIX);
 *       ...
 *
 *
 * The Solution:
 * Prefix all a exported symbols with "CIET_". This is similar to
 * what Mozilla does for some common libs:
 * http://lxr.mozilla.org/seamonkey/source/modules/libimg/png/mozpngconf.h#115
 *
 * The list of relevant exported symbols can be had with this command:
 * 
       nm pyexpat.so \
           | grep -v " [a-zBUA] " \
           | grep -v "_fini\|_init\|initpyexpat"
 *
 * If any of those symbols are NOT prefixed with "CIET_" then
 * a #define should be added for it here.
 */

#ifndef CIETEXPATNS_H
#define CIETEXPATNS_H

#define XML_DefaultCurrent              CIET_XML_DefaultCurrent
#define XML_ErrorString                 CIET_XML_ErrorString
#define XML_ExpatVersion                CIET_XML_ExpatVersion
#define XML_ExpatVersionInfo            CIET_XML_ExpatVersionInfo
#define XML_ExternalEntityParserCreate  CIET_XML_ExternalEntityParserCreate
#define XML_FreeContentModel            CIET_XML_FreeContentModel
#define XML_GetBase                     CIET_XML_GetBase
#define XML_GetBuffer                   CIET_XML_GetBuffer
#define XML_GetCurrentByteCount         CIET_XML_GetCurrentByteCount
#define XML_GetCurrentByteIndex         CIET_XML_GetCurrentByteIndex
#define XML_GetCurrentColumnNumber      CIET_XML_GetCurrentColumnNumber
#define XML_GetCurrentLineNumber        CIET_XML_GetCurrentLineNumber
#define XML_GetErrorCode                CIET_XML_GetErrorCode
#define XML_GetFeatureList              CIET_XML_GetFeatureList
#define XML_GetIdAttributeIndex         CIET_XML_GetIdAttributeIndex
#define XML_GetInputContext             CIET_XML_GetInputContext
#define XML_GetParsingStatus            CIET_XML_GetParsingStatus
#define XML_GetSpecifiedAttributeCount  CIET_XML_GetSpecifiedAttributeCount
#define XmlGetUtf16InternalEncoding     CIET_XmlGetUtf16InternalEncoding
#define XmlGetUtf16InternalEncodingNS   CIET_XmlGetUtf16InternalEncodingNS
#define XmlGetUtf8InternalEncoding      CIET_XmlGetUtf8InternalEncoding
#define XmlGetUtf8InternalEncodingNS    CIET_XmlGetUtf8InternalEncodingNS
#define XmlInitEncoding                 CIET_XmlInitEncoding
#define XmlInitEncodingNS               CIET_XmlInitEncodingNS
#define XmlInitUnknownEncoding          CIET_XmlInitUnknownEncoding
#define XmlInitUnknownEncodingNS        CIET_XmlInitUnknownEncodingNS
#define XML_MemFree                     CIET_XML_MemFree
#define XML_MemMalloc                   CIET_XML_MemMalloc
#define XML_MemRealloc                  CIET_XML_MemRealloc
#define XML_Parse                       CIET_XML_Parse
#define XML_ParseBuffer                 CIET_XML_ParseBuffer
#define XML_ParserCreate                CIET_XML_ParserCreate
#define XML_ParserCreate_MM             CIET_XML_ParserCreate_MM
#define XML_ParserCreateNS              CIET_XML_ParserCreateNS
#define XML_ParserFree                  CIET_XML_ParserFree
#define XML_ParserReset                 CIET_XML_ParserReset
#define XmlParseXmlDecl                 CIET_XmlParseXmlDecl
#define XmlParseXmlDeclNS               CIET_XmlParseXmlDeclNS
#define XmlPrologStateInit              CIET_XmlPrologStateInit
#define XmlPrologStateInitExternalEntity    CIET_XmlPrologStateInitExternalEntity
#define XML_ResumeParser                CIET_XML_ResumeParser
#define XML_SetAttlistDeclHandler       CIET_XML_SetAttlistDeclHandler
#define XML_SetBase                     CIET_XML_SetBase
#define XML_SetCdataSectionHandler      CIET_XML_SetCdataSectionHandler
#define XML_SetCharacterDataHandler     CIET_XML_SetCharacterDataHandler
#define XML_SetCommentHandler           CIET_XML_SetCommentHandler
#define XML_SetDefaultHandler           CIET_XML_SetDefaultHandler
#define XML_SetDefaultHandlerExpand     CIET_XML_SetDefaultHandlerExpand
#define XML_SetDoctypeDeclHandler       CIET_XML_SetDoctypeDeclHandler
#define XML_SetElementDeclHandler       CIET_XML_SetElementDeclHandler
#define XML_SetElementHandler           CIET_XML_SetElementHandler
#define XML_SetEncoding                 CIET_XML_SetEncoding
#define XML_SetEndCdataSectionHandler   CIET_XML_SetEndCdataSectionHandler
#define XML_SetEndDoctypeDeclHandler    CIET_XML_SetEndDoctypeDeclHandler
#define XML_SetEndElementHandler        CIET_XML_SetEndElementHandler
#define XML_SetEndNamespaceDeclHandler  CIET_XML_SetEndNamespaceDeclHandler
#define XML_SetEntityDeclHandler        CIET_XML_SetEntityDeclHandler
#define XML_SetExternalEntityRefHandler CIET_XML_SetExternalEntityRefHandler
#define XML_SetExternalEntityRefHandlerArg  CIET_XML_SetExternalEntityRefHandlerArg
#define XML_SetHashSalt                 CIET_XML_SetHashSalt
#define XML_SetNamespaceDeclHandler     CIET_XML_SetNamespaceDeclHandler
#define XML_SetNotationDeclHandler      CIET_XML_SetNotationDeclHandler
#define XML_SetNotStandaloneHandler     CIET_XML_SetNotStandaloneHandler
#define XML_SetParamEntityParsing       CIET_XML_SetParamEntityParsing
#define XML_SetProcessingInstructionHandler CIET_XML_SetProcessingInstructionHandler
#define XML_SetReturnNSTriplet          CIET_XML_SetReturnNSTriplet
#define XML_SetSkippedEntityHandler     CIET_XML_SetSkippedEntityHandler
#define XML_SetStartCdataSectionHandler CIET_XML_SetStartCdataSectionHandler
#define XML_SetStartDoctypeDeclHandler  CIET_XML_SetStartDoctypeDeclHandler
#define XML_SetStartElementHandler      CIET_XML_SetStartElementHandler
#define XML_SetStartNamespaceDeclHandler    CIET_XML_SetStartNamespaceDeclHandler
#define XML_SetUnknownEncodingHandler   CIET_XML_SetUnknownEncodingHandler
#define XML_SetUnparsedEntityDeclHandler    CIET_XML_SetUnparsedEntityDeclHandler
#define XML_SetUserData                 CIET_XML_SetUserData
#define XML_SetXmlDeclHandler           CIET_XML_SetXmlDeclHandler
#define XmlSizeOfUnknownEncoding        CIET_XmlSizeOfUnknownEncoding
#define XML_StopParser                  CIET_XML_StopParser
#define XML_UseForeignDTD               CIET_XML_UseForeignDTD
#define XML_UseParserAsHandlerArg       CIET_XML_UseParserAsHandlerArg
#define XmlUtf16Encode                  CIET_XmlUtf16Encode
#define XmlUtf8Encode                   CIET_XmlUtf8Encode


#endif /* !CIETEXPATNS_H */

